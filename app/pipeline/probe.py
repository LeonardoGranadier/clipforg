"""Lê as informações do vídeo com ffprobe."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from app.errors import ClipForgeError
from app.models import VideoInfo
from app.pipeline.ffmpeg import explain_failure, require


def _fps(rate: str) -> float:
    try:
        num, den = rate.split("/")
        return float(num) / float(den) if float(den) else 0.0
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(path: Path) -> VideoInfo:
    require("ffprobe")
    if not path.is_file():
        raise ClipForgeError(f"Arquivo não encontrado: {path}")
    res = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)],
        capture_output=True, text=True,
    )
    if res.returncode != 0:
        raise ClipForgeError(explain_failure(res.stderr))
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError as e:
        raise ClipForgeError("Não foi possível ler as informações do vídeo.") from e

    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"
                  and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise ClipForgeError("O arquivo não tem trilha de vídeo.")
    has_audio = any(s.get("codec_type") == "audio" for s in streams)

    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise ClipForgeError("Não foi possível determinar a duração do vídeo (arquivo corrompido?).")

    width, height = int(video.get("width", 0)), int(video.get("height", 0))
    # Vídeos de celular podem vir com rotação nos metadados: trocamos largura/altura.
    rotation = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rotation = abs(int(sd["rotation"]))
    if rotation in (90, 270):
        width, height = height, width

    return VideoInfo(
        duration=duration,
        width=width,
        height=height,
        fps=_fps(video.get("avg_frame_rate", "0/1")) or _fps(video.get("r_frame_rate", "0/1")),
        has_audio=has_audio,
    )
