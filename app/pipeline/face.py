"""Recorte vertical seguindo o rosto (face-follow).

1. Amostra o trecho a 4 quadros/s em baixa resolução (FFmpeg -> numpy).
2. Detecta rostos com o YuNet (OpenCV) e fica com o maior de cada quadro.
3. Suaviza a posição: zona morta com histerese, limite de velocidade e salto em troca de câmera.
   Sem rosto: mantém a última posição por 1 s e depois volta ao centro devagar.
4. Gera um arquivo de comandos para o filtro `sendcmd`, que move o `crop` quadro a quadro.
"""
from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.errors import Cancelled, ClipForgeError
from app.models import VideoInfo

MODEL = settings.root / "models" / "face_detection_yunet_2023mar.onnx"

SAMPLE_FPS = 4            # análise: 4 quadros por segundo (~a cada 0,25 s)
ANALYSIS_WIDTH = 640      # largura dos quadros analisados
MIN_SCORE = 0.6           # confiança mínima do detector
HOLD_SECONDS = 1.0        # sem rosto: mantém a posição por 1 s
DEAD_ZONE = 0.05          # o recorte só começa a se mover se o rosto sair 5% da largura do centro...
SETTLE = 0.015            # ...e, uma vez em movimento, só para quando recentralizar (histerese)
MAX_SPEED = 0.35          # velocidade máxima do recorte: 35% da largura por segundo
FOLLOW = 0.35             # quanto se aproxima do alvo a cada amostra (0..1)
JUMP = 0.25               # salto instantâneo se o rosto mudar >25% da largura...
JUMP_SAMPLES = 2          # ...e continuar lá por 2 amostras (troca de câmera/plano)


@dataclass
class FacePath:
    """Centro horizontal do recorte (0..1 da largura) a cada 1/SAMPLE_FPS segundos."""
    centers: list[float]
    detected: int          # quantas amostras tiveram rosto


# --- detecção ---------------------------------------------------------------

def _detector(width: int, height: int):
    os.environ.setdefault("OPENCV_LOG_LEVEL", "ERROR")  # precisa vir antes do import
    try:
        import cv2
    except ImportError as e:
        raise ClipForgeError("OpenCV não está instalado. Rode: uv pip install -r requirements.txt") from e
    if not MODEL.exists():
        raise ClipForgeError(f"Modelo de detecção de rosto não encontrado: {MODEL}")
    try:
        cv2.setLogLevel(2)  # só erros
    except AttributeError:
        pass
    return cv2.FaceDetectorYN.create(str(MODEL), "", (width, height), MIN_SCORE, 0.3, 50)


def sample_faces(
    src: Path, start: float, end: float, info: VideoInfo,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[float | None]:
    """Centro x (0..1) do maior rosto em cada amostra, ou None se não houver rosto."""
    import numpy as np

    w = ANALYSIS_WIDTH
    h = max(2, round(info.height * w / info.width / 2) * 2)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
           "-an", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    detector = _detector(w, h)
    expected = max(1, int((end - start) * SAMPLE_FPS))
    frame_bytes = w * h * 3
    out: list[float | None] = []
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            _, faces = detector.detect(frame)
            if faces is None or len(faces) == 0:
                out.append(None)
            else:
                x, _, fw, fh = max(faces, key=lambda f: f[2] * f[3])[:4]  # o maior rosto
                out.append(float(min(1.0, max(0.0, (x + fw / 2) / w))))
            if on_progress:
                on_progress(min(1.0, len(out) / expected))
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode not in (0, -9) and not out:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise ClipForgeError(f"Não foi possível analisar o vídeo para seguir o rosto. {err[-200:]}")
    return out


# --- suavização ---------------------------------------------------------------

def smooth_path(raw: list[float | None], fps: float = SAMPLE_FPS) -> FacePath:
    """Transforma as detecções (com falhas e ruído) num caminho estável para o recorte."""
    hold = round(HOLD_SECONDS * fps)
    max_step = MAX_SPEED / fps
    first = next((r for r in raw if r is not None), 0.5)
    pos, last_seen, missing, far, outside, moving = first, first, 0, 0, 0, False
    centers: list[float] = []
    for r in raw:
        if r is not None:
            last_seen, missing = r, 0
            target = r
        else:
            missing += 1
            target = last_seen if missing <= hold else 0.5  # depois de 1 s sem rosto, volta ao centro

        diff = target - pos
        far = far + 1 if r is not None and abs(diff) > JUMP else 0
        if far >= JUMP_SAMPLES:
            pos, far, moving = target, 0, False   # troca de câmera: pula direto
        else:
            outside = outside + 1 if abs(diff) > DEAD_ZONE else 0
            if outside >= 2:          # 1 amostra isolada (falso positivo) não move o recorte
                moving = True
            elif abs(diff) < SETTLE:
                moving = False
            if moving:
                pos += max(-max_step, min(max_step, diff * FOLLOW))
        centers.append(pos)
    return FacePath(centers=centers, detected=sum(r is not None for r in raw))


def crop_box(info: VideoInfo) -> tuple[int, int]:
    """Largura e altura do recorte 9:16 no vídeo original (valores pares)."""
    ch = info.height
    cw = min(info.width, round(ch * 9 / 16))
    return cw - cw % 2, ch - ch % 2


def crop_positions(path: FacePath, info: VideoInfo, duration: float, out_fps: int = 30) -> list[int]:
    """x do recorte (pixels do vídeo original) para cada quadro de saída, interpolando as amostras."""
    cw, _ = crop_box(info)
    max_x = info.width - cw
    c = path.centers or [0.5]
    n = max(1, round(duration * out_fps))
    xs = []
    for k in range(n):
        s = k / out_fps * SAMPLE_FPS            # posição em "amostras"
        i = min(int(s), len(c) - 1)
        j = min(i + 1, len(c) - 1)
        center = c[i] + (c[j] - c[i]) * (s - int(s)) if i != j else c[i]
        x = round(center * info.width - cw / 2)
        xs.append(max(0, min(max_x, x)))
    return xs


def sendcmd_script(xs: list[int], out_fps: int = 30, target: str = "crop@face") -> str:
    """Comandos para o filtro sendcmd: só escreve quando o x muda."""
    lines, last = [], None
    for k, x in enumerate(xs):
        if x != last:
            lines.append(f"{k / out_fps:.4f} {target} x {x};")
            last = x
    return "\n".join(lines) + "\n"
