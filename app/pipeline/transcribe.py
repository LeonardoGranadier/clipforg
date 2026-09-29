"""Transcrição com faster-whisper, com tempo de cada palavra."""
from __future__ import annotations

import json
import logging
import os
import threading
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.errors import Cancelled, ClipForgeError
from app.models import Segment, Transcript, Word
from app.pipeline.ffmpeg import run_ffmpeg

log = logging.getLogger(__name__)

MIN_WORD = 0.05  # duração mínima de uma palavra, em segundos


def fix_word_times(words: list[Word], seg_start: float, seg_end: float) -> list[Word]:
    """Garante tempos crescentes e end > start, interpolando entre as palavras vizinhas."""
    fixed: list[Word] = []
    for i, w in enumerate(words):
        lo = fixed[-1].end if fixed else seg_start
        start = w.start if w.start is not None and w.start >= lo else lo
        end = w.end
        if end is None or end <= start:
            nxt = next((n.start for n in words[i + 1:] if n.start is not None and n.start > start), seg_end)
            end = max(start + MIN_WORD, min(nxt, start + 0.3))
        fixed.append(Word(word=w.word, start=round(start, 3), end=round(end, 3), prob=w.prob))
    return fixed


def _device() -> tuple[str, str]:
    want = settings.whisper_device
    if want in ("cuda", "auto"):
        try:
            import ctranslate2
            if ctranslate2.get_cuda_device_count() > 0:
                types = ctranslate2.get_supported_compute_types("cuda")
                return "cuda", "float16" if "float16" in types else "float32"
        except Exception:
            pass
        if want == "cuda":
            log.warning("WHISPER_DEVICE=cuda, mas nenhuma GPU utilizável; usando CPU")
    return "cpu", "int8"


def extract_audio(video: Path, wav: Path, cancel: threading.Event | None = None) -> None:
    run_ffmpeg(
        ["-y", "-i", str(video), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)],
        log_path=settings.logs_dir / "extract_audio.log", cancel=cancel,
    )


def load_transcript(path: Path) -> Transcript:
    return Transcript.model_validate_json(path.read_text(encoding="utf-8"))


def transcribe(
    video: Path,
    out_path: Path,
    *,
    language: str | None = None,
    model_name: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Transcript:
    """Transcreve o vídeo e salva em `out_path`. Se o arquivo já existe, só carrega (cache)."""
    if out_path.exists():
        return load_transcript(out_path)

    lang = language or settings.language
    lang_arg = None if lang == "auto" else lang
    model_name = model_name or settings.whisper_model

    wav = out_path.parent / "audio.tmp.wav"
    try:
        extract_audio(video, wav, cancel)
        os.environ.setdefault("HF_HUB_VERBOSITY", "error")  # esconde avisos do download do modelo
        try:
            from faster_whisper import WhisperModel
        except ImportError as e:
            raise ClipForgeError("faster-whisper não está instalado. Rode: uv pip install -r requirements.txt") from e

        device, compute_type = _device()
        log.info("Whisper: modelo=%s device=%s compute=%s", model_name, device, compute_type)
        try:
            model = WhisperModel(model_name, device=device, compute_type=compute_type,
                                 cpu_threads=os.cpu_count() or 4)
        except Exception as e:
            raise ClipForgeError(
                f"Não foi possível carregar o modelo Whisper '{model_name}'. "
                f"Na primeira vez ele é baixado da internet; confira sua conexão. ({e})"
            ) from e

        segments_iter, info = model.transcribe(
            str(wav), language=lang_arg, word_timestamps=True, vad_filter=True, beam_size=5,
        )
        segments: list[Segment] = []
        for seg in segments_iter:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            raw = [Word(word=w.word.strip(), start=w.start, end=w.end, prob=round(w.probability, 3))
                   for w in (seg.words or []) if w.word.strip()]
            if not raw:
                continue
            words = fix_word_times(raw, seg.start, seg.end)
            segments.append(Segment(
                id=len(segments), start=words[0].start, end=words[-1].end,
                text=seg.text.strip(), words=words,
            ))
            if on_progress and info.duration:
                on_progress(min(1.0, seg.end / info.duration))
    finally:
        wav.unlink(missing_ok=True)

    if not segments:
        raise ClipForgeError("Nenhuma fala foi encontrada no áudio do vídeo.")

    transcript = Transcript(language=info.language, duration=info.duration, segments=segments)
    out_path.write_text(json.dumps(transcript.model_dump(), ensure_ascii=False, indent=1), encoding="utf-8")
    if on_progress:
        on_progress(1.0)
    return transcript
