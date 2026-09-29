"""Reenquadramento vertical 9:16 (1080x1920): fit, center e blur."""
from __future__ import annotations

from typing import Literal

from app.models import VideoInfo

VerticalMode = Literal["fit", "center", "blur"]
MODES: tuple[str, ...] = ("fit", "center", "blur")
OUT_W, OUT_H, OUT_FPS = 1080, 1920, 30


def is_vertical(info: VideoInfo) -> bool:
    return info.height > info.width


def vertical_filter(mode: VerticalMode, info: VideoInfo) -> str:
    """Filtro FFmpeg (-vf) que transforma o vídeo em 1080x1920 a 30 fps.

    Vídeos que já são verticais não são reenquadrados: só se ajustam à tela (fit).
    """
    if is_vertical(info):
        mode = "fit"
    W, H = OUT_W, OUT_H
    if mode == "fit":
        # Barras pretas. force_original_aspect_ratio também cobre vídeos mais "altos" que 9:16.
        chain = (f"scale={W}:{H}:force_original_aspect_ratio=decrease,"
                 f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:black")
    elif mode == "center":
        # Recorte central 9:16. min() evita erro se o vídeo for mais estreito que 9:16.
        chain = (r"crop=w='min(iw\,ih*9/16)':h='min(ih\,iw*16/9)',"
                 f"scale={W}:{H}")
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
