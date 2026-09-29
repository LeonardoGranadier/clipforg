"""Corte com FFmpeg, recodificando para ter precisão de quadro."""
from __future__ import annotations

import threading
from collections.abc import Callable
from pathlib import Path

from app.config import settings
from app.errors import ClipForgeError
from app.pipeline.ffmpeg import filter_path, run_ffmpeg


def ass_filter(ass_path: Path) -> str:
    return f"ass=filename={filter_path(ass_path)}:fontsdir={filter_path(settings.fonts_dir)}"


def cut_args(src: Path, dst: Path, start: float, end: float, vf: str | None = None) -> list[str]:
    return [
        "-y", "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
        "-map", "0:v:0", "-map", "0:a:0?",
        *(["-vf", vf] if vf else []),
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "160k", "-movflags", "+faststart", str(dst),
    ]


def cut(
    src: Path,
    dst: Path,
    start: float,
    end: float,
    *,
    log_name: str,
    vf: str | None = None,
    ass_path: Path | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> Path:
    """Corta [start, end].

    `vf`: filtro de vídeo aplicado antes da legenda (ex.: reenquadramento vertical).
    `ass_path`: grava a legenda no vídeo (tempos relativos ao início do corte).
    """
    if end <= start:
        raise ClipForgeError(f"Corte inválido: fim ({end:.2f}s) antes do início ({start:.2f}s).")
    dst.parent.mkdir(parents=True, exist_ok=True)
    tmp = dst.with_name(dst.stem + ".part" + dst.suffix)
    filters = ",".join(f for f in (vf, ass_filter(ass_path) if ass_path else None) if f) or None
    try:
        run_ffmpeg(cut_args(src, tmp, start, end, filters), log_path=settings.logs_dir / f"{log_name}.log",
                   duration=end - start, on_progress=on_progress, cancel=cancel)
        tmp.replace(dst)
    finally:
        tmp.unlink(missing_ok=True)
    return dst
