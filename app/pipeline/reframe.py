"""Reenquadramento vertical 9:16 (1080x1920): fit, center, blur e face (segue o rosto)."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from app.models import VideoInfo
from app.pipeline.ffmpeg import filter_path

VerticalMode = Literal["fit", "center", "blur", "face"]
MODES: tuple[str, ...] = ("fit", "center", "blur", "face")
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30
RESOLUTIONS: dict[str, tuple[int, int]] = {"1080p": (1080, 1920), "720p": (720, 1280)}


def is_vertical(info: VideoInfo) -> bool:
    return info.height > info.width


def vertical_filter(
    mode: VerticalMode,
    info: VideoInfo,
    size: tuple[int, int] = (OUT_W, OUT_H),
    face: tuple[Path, int, int, int] | None = None,
) -> str:
    """Filtro FFmpeg (-vf) que transforma o vídeo em 9:16 (padrão 1080x1920) a 30 fps.

    Vídeos que já são verticais não são reenquadrados: só se ajustam à tela (fit).
    `face` (modo face): (arquivo do sendcmd, largura e altura do recorte, x inicial).
    """
    if is_vertical(info):
        mode = "fit"
    W, H = size
    if mode == "fit":
        # Barras pretas. force_original_aspect_ratio também cobre vídeos mais "altos" que 9:16.
        chain = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
    elif mode == "center":
        # Recorte central 9:16. min() evita erro se o vídeo for mais estreito que 9:16.
        chain = (r"crop=w='min(iw\,ih*9/16)':h='min(ih\,iw*16/9)',"
                 f"scale={W}:{H}")
    elif mode == "face":
        if face is None:
            raise ValueError("modo face precisa do caminho calculado (face=...)")
        cmds, cw, ch, x0 = face
        y = (info.height - ch) // 2
        # sendcmd muda o x do crop ao longo do tempo (tempos relativos ao início do corte)
        chain = (f"sendcmd=f={filter_path(cmds)},"
                 f"crop@face=w={cw}:h={ch}:x={x0}:y={y},scale={W}:{H}")
    elif mode == "blur":
        # Fundo: o próprio vídeo preenchendo a tela, desfocado (em resolução baixa, que é mais rápido).
        # Frente: o vídeo inteiro na largura 1080, no centro.
        chain = (f"split=2[bg][fg];"
                 f"[bg]scale={W // 4}:{H // 4}:force_original_aspect_ratio=increase,"
                 f"crop={W // 4}:{H // 4},boxblur=12:2,scale={W}:{H},eq=brightness=-0.08[bgb];"
                 f"[fg]scale={W}:{H}:force_original_aspect_ratio=decrease[fgs];"
                 f"[bgb][fgs]overlay=(W-w)/2:(H-h)/2")
    else:
        raise ValueError(f"modo vertical desconhecido: {mode}")
    return f"{chain},fps={OUT_FPS},setsar=1"
