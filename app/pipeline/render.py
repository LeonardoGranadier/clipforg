"""Orquestra o render de um corte: legenda (.ass/.srt) + corte com FFmpeg."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

import logging

from app.models import Clip, Transcript, VideoInfo
from app.pipeline import captions
from app.pipeline.cut import cut
from app.errors import ClipForgeError
from app.pipeline import face
from app.pipeline.reframe import RESOLUTIONS, VerticalMode, is_vertical, vertical_filter
from app.projects import Project, slugify


log = logging.getLogger(__name__)


def caption_words(project: Project, transcript: Transcript):
    """Palavras da transcrição com as correções do usuário (captions_edited.json) aplicadas."""
    return captions.apply_edits(transcript.all_words(), captions.load_edits(project.captions_edits_path))


def render_clip(
    project: Project,
    clip: Clip,
    transcript: Transcript,
    info: VideoInfo,
    *,
    style: str | None = None,
    vertical: VerticalMode | None = None,
    resolution: str = "1080p",
    speed: str | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Gera o .mp4 do corte.

    `style`: também gera .ass/.srt e grava a legenda no vídeo.
    `vertical`: saída 9:16 no modo fit, center ou blur; sem ele, mantém o formato original.
    `resolution`: tamanho da saída vertical ("1080p" = 1080x1920, "720p" = 720x1280).
    """
    if resolution not in RESOLUTIONS:
        raise ClipForgeError(f"Resolução inválida: {resolution}. Opções: {', '.join(RESOLUTIONS)}")
    base = project.clips_dir / f"{clip.id}-{slugify(clip.title, 30)}"
    size = RESOLUTIONS[resolution]
    if vertical == "face" and is_vertical(info):
        vertical = "fit"  # vídeo já vertical: não há o que seguir
    face_args = None
    render_progress = on_progress
    if vertical == "face":
        # 1ª parte do progresso (25%) é a análise dos rostos; o resto é o render.
        cmds = base.with_suffix(".face.txt")
        face_args = follow_face(project, clip, info, cmds, transcript,
                                on_progress=(lambda f: on_progress(0.25 * f)) if on_progress else None,
                                cancel=cancel)
        render_progress = (lambda f: on_progress(0.25 + 0.75 * f)) if on_progress else None
    vf = vertical_filter(vertical, info, size, face=face_args) if vertical else None
    width, height = size if vertical else (info.width, info.height)
    ass_path = None
    if style:
        preset = captions.load_preset(style)
        words = captions.words_in_range(caption_words(project, transcript), clip.start, clip.end)
        ass_path = base.with_suffix(".ass")
        captions.write_caption_files(words, preset, ass_path, base.with_suffix(".srt"),
                                     width=width, height=height)
    try:
        return cut(project.source, base.with_suffix(".mp4"), clip.start, clip.end,
                   log_name=f"{project.id}-{clip.id}", vf=vf, ass_path=ass_path, speed=speed,
                   on_progress=render_progress, cancel=cancel)
    finally:
        if face_args:
            face_args[0].unlink(missing_ok=True)  # arquivo temporário do sendcmd


def follow_face(
    project: Project, clip: Clip, info: VideoInfo, cmds: Path, transcript: Transcript | None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> tuple[Path, int, int, int]:
    """Analisa os rostos do corte e grava os comandos do recorte. Retorna (arquivo, w, h, x inicial).

    A transcrição diz quando há fala; com várias pessoas, o recorte segue quem mexe a boca nesses momentos.
    """
    samples = face.analyze(project.source, clip.start, clip.end, info, on_progress=on_progress, cancel=cancel)
    words = transcript.all_words() if transcript else None
    path = face.follow_path(samples, words, clip.start)
    log.info("Seguir o rosto (%s %s): %d pessoa(s), trocas de pessoa em %s s",
             project.id, clip.id, path.people, path.switches)
    xs = face.crop_positions(path, info, clip.end - clip.start)
    cmds.write_text(face.sendcmd_script(xs), encoding="utf-8")
    cw, ch = face.crop_box(info)
    return cmds, cw, ch, xs[0]


def export_full_captions(project: Project, transcript: Transcript, info: VideoInfo, style: str) -> tuple[Path, Path]:
    """Modo só legendas: .ass e .srt do vídeo inteiro, sem cortar."""
    preset = captions.load_preset(style)
    words = caption_words(project, transcript)
    name = slugify(project.source.stem)
    ass_path = project.exports_dir / f"{name}.{style}.ass"
    srt_path = project.exports_dir / f"{name}.srt"
    captions.write_caption_files(words, preset, ass_path, srt_path, width=info.width, height=info.height)
    return ass_path, srt_path
