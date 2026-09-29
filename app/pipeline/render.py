"""Orquestra o render de um corte: legenda (.ass/.srt) + corte com FFmpeg."""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from app.models import Clip, Transcript, VideoInfo
from app.pipeline import captions
from app.pipeline.cut import cut
from app.pipeline.reframe import OUT_H, OUT_W, VerticalMode, vertical_filter
from app.projects import Project, slugify


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
    on_progress: Callable[[float], None] | None = None,
) -> Path:
    """Gera o .mp4 do corte.

    `style`: também gera .ass/.srt e grava a legenda no vídeo.
    `vertical`: saída 1080x1920 no modo fit, center ou blur; sem ele, mantém o formato original.
    """
    base = project.clips_dir / f"{clip.id}-{slugify(clip.title, 30)}"
    vf = vertical_filter(vertical, info) if vertical else None
    width, height = (OUT_W, OUT_H) if vertical else (info.width, info.height)
    ass_path = None
    if style:
        preset = captions.load_preset(style)
        words = captions.words_in_range(caption_words(project, transcript), clip.start, clip.end)
        ass_path = base.with_suffix(".ass")
        captions.write_caption_files(words, preset, ass_path, base.with_suffix(".srt"),
                                     width=width, height=height)
    return cut(project.source, base.with_suffix(".mp4"), clip.start, clip.end,
               log_name=f"{project.id}-{clip.id}", vf=vf, ass_path=ass_path, on_progress=on_progress)


def export_full_captions(project: Project, transcript: Transcript, info: VideoInfo, style: str) -> tuple[Path, Path]:
    """Modo só legendas: .ass e .srt do vídeo inteiro, sem cortar."""
    preset = captions.load_preset(style)
    words = caption_words(project, transcript)
    name = slugify(project.source.stem)
    ass_path = project.exports_dir / f"{name}.{style}.ass"
    srt_path = project.exports_dir / f"{name}.srt"
    captions.write_caption_files(words, preset, ass_path, srt_path, width=info.width, height=info.height)
    return ass_path, srt_path
