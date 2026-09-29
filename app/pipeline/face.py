"""Recorte vertical seguindo o rosto de quem fala (face-follow).

1. Amostra o trecho a 8 quadros/s em baixa resolução (FFmpeg -> numpy).
2. Detecta rostos com o YuNet (OpenCV) e acompanha cada pessoa ao longo do tempo (trilhas).
3. Com várias pessoas, escolhe quem está falando: mede o quanto a boca de cada uma se mexe
   (recorte da boca alinhado pelos pontos do rosto, para não confundir com movimento da cabeça)
   enquanto a transcrição indica fala. A troca de pessoa exige uma diferença clara por ~0,5 s.
   Com uma pessoa só, segue ela.
4. Suaviza a posição: zona morta com histerese, limite de velocidade e salto em troca de
   câmera ou de pessoa. Sem rosto: mantém a posição por 1 s e depois volta ao centro devagar.
5. Gera um arquivo de comandos para o filtro `sendcmd`, que move o `crop` quadro a quadro.
"""
from __future__ import annotations

import os
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

from app.config import settings
from app.errors import Cancelled, ClipForgeError
from app.models import VideoInfo, Word

MODEL = settings.root / "models" / "face_detection_yunet_2023mar.onnx"

SAMPLE_FPS = 8            # análise: 8 quadros por segundo (a boca muda rápido)
ANALYSIS_WIDTH = 640      # largura dos quadros analisados
MIN_SCORE = 0.6           # confiança mínima do detector

# suavização do recorte (em segundos, para não depender de SAMPLE_FPS)
HOLD_SECONDS = 1.0        # sem rosto: mantém a posição por 1 s
DEAD_ZONE = 0.05          # o recorte só começa a se mover se o rosto sair 5% da largura do centro...
SETTLE = 0.015            # ...e, uma vez em movimento, só para quando recentralizar (histerese)
OUTSIDE_SECONDS = 0.5     # ...e só se o rosto ficar fora da zona por 0,5 s (ignora falso positivo)
MAX_SPEED = 0.35          # velocidade máxima do recorte: 35% da largura por segundo
FOLLOW_PER_SECOND = 0.82  # fração da distância ao alvo percorrida em 1 s
JUMP = 0.25               # salto instantâneo se o alvo mudar >25% da largura...
JUMP_SAMPLES = 2          # ...e continuar lá por 2 amostras (troca de câmera ou de pessoa)

# escolha de quem fala
ACTIVITY_WINDOW = 0.75    # média do movimento da boca nos últimos 0,75 s
SWITCH_RATIO = 1.5        # outra pessoa precisa mexer a boca 50% mais que a atual...
SWITCH_SECONDS = 0.5      # ...por 0,5 s seguidos para o recorte trocar de pessoa
MIN_ACTIVITY = 0.08       # abaixo disso ninguém está claramente falando
TRACK_MATCH = 0.6         # um rosto continua na mesma trilha se andou menos que 60% da largura dele
TRACK_TIMEOUT = 1.0       # trilha some depois de 1 s sem ser vista


@dataclass
class Face:
    """Um rosto numa amostra (coordenadas em 0..1 da largura/altura do vídeo)."""
    cx: float
    w: float
    activity: float | None = None   # movimento da boca desde a amostra anterior (None = sem referência)


@dataclass
class FacePath:
    """Centro horizontal do recorte (0..1 da largura) a cada 1/SAMPLE_FPS segundos."""
    centers: list[float]
    detected: int          # quantas amostras tiveram rosto
    people: int = 0        # quantas pessoas diferentes foram vistas
    switches: list[float] = field(default_factory=list)  # instantes (s) em que trocou de pessoa


# --- detecção e trilhas ---------------------------------------------------------

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


def mouth_patch(gray, face) -> "object | None":
    """Recorte da boca (normalizado, 32x24) alinhado pelos pontos do YuNet: nariz e cantos da boca."""
    import cv2
    import numpy as np

    _, _, w, _ = face[:4]
    nose_y = face[9]
    rx, ry, lx, ly = face[10:14]
    mouth_y = (ry + ly) / 2
    d = max(2.0, mouth_y - nose_y)
    x0, x1 = min(rx, lx) - 0.12 * w, max(rx, lx) + 0.12 * w
    y0, y1 = nose_y + 0.3 * d, mouth_y + 1.2 * d
    h_img, w_img = gray.shape
    x0, x1 = int(max(0, x0)), int(min(w_img, x1))
    y0, y1 = int(max(0, y0)), int(min(h_img, y1))
    if x1 - x0 < 4 or y1 - y0 < 4:
        return None
    patch = cv2.resize(gray[y0:y1, x0:x1], (32, 24), interpolation=cv2.INTER_AREA).astype(np.float32)
    return (patch - patch.mean()) / (patch.std() + 8.0)  # +8: evita amplificar ruído em áreas lisas


def analyze(
    src: Path, start: float, end: float, info: VideoInfo,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[dict[int, Face]]:
    """Para cada amostra, os rostos visíveis por trilha: {id_da_pessoa: Face}."""
    import cv2
    import numpy as np

    w = ANALYSIS_WIDTH
    h = max(2, round(info.height * w / info.width / 2) * 2)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin",
           "-ss", f"{start:.3f}", "-to", f"{end:.3f}", "-i", str(src),
           "-an", "-vf", f"fps={SAMPLE_FPS},scale={w}:{h}", "-pix_fmt", "bgr24", "-f", "rawvideo", "pipe:1"]
    detector = _detector(w, h)
    expected = max(1, int((end - start) * SAMPLE_FPS))
    frame_bytes = w * h * 3
    timeout = round(TRACK_TIMEOUT * SAMPLE_FPS)

    samples: list[dict[int, Face]] = []
    tracks: dict[int, dict] = {}   # id -> {cx, w, last (índice da amostra), patch}
    next_id = 0
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        assert proc.stdout is not None
        while True:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            i = len(samples)
            frame = np.frombuffer(buf, np.uint8).reshape(h, w, 3)
            _, faces = detector.detect(frame)
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            current: dict[int, Face] = {}
            free = {tid for tid, t in tracks.items() if i - t["last"] <= timeout}
            # maiores primeiro: disputam as trilhas antes
            for f in sorted(faces if faces is not None else [], key=lambda f: -f[2] * f[3]):
                cx, fw = (f[0] + f[2] / 2) / w, f[2] / w
                tid = min(free, key=lambda t: abs(tracks[t]["cx"] - cx), default=None)
                if tid is None or abs(tracks[tid]["cx"] - cx) > TRACK_MATCH * max(fw, tracks[tid]["w"]):
                    tid, next_id = next_id, next_id + 1
                    tracks[tid] = {"cx": cx, "w": fw, "last": -99, "patch": None}
                free.discard(tid)
                patch = mouth_patch(gray, f)
                t = tracks[tid]
                activity = None
                if patch is not None and t["patch"] is not None and t["last"] == i - 1:
                    activity = float(np.abs(patch - t["patch"]).mean())
                t.update(cx=cx, w=fw, last=i, patch=patch)
                current[tid] = Face(cx=float(min(1.0, max(0.0, cx))), w=float(fw), activity=activity)
            samples.append(current)
            if on_progress:
                on_progress(min(1.0, len(samples) / expected))
    finally:
        proc.stdout.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
    if proc.returncode not in (0, -9) and not samples:
        err = proc.stderr.read().decode(errors="replace") if proc.stderr else ""
        raise ClipForgeError(f"Não foi possível analisar o vídeo para seguir o rosto. {err[-200:]}")
    return samples


# --- quem está falando -----------------------------------------------------------

def speech_mask(words: Sequence[Word] | None, start: float, n: int, fps: float = SAMPLE_FPS,
                margin: float = 0.15) -> list[bool]:
    """Para cada amostra, se há fala (pela transcrição). Sem transcrição: considera que sempre há."""
    if not words:
        return [True] * n
    ws = sorted(words, key=lambda w: w.start)
    out, j = [], 0
    for i in range(n):
        t = start + i / fps
        while j < len(ws) and ws[j].end + margin < t:
            j += 1
        out.append(j < len(ws) and ws[j].start - margin <= t)
    return out


def choose_targets(samples: list[dict[int, Face]], speech: list[bool],
                   fps: float = SAMPLE_FPS) -> tuple[list[float | None], list[int]]:
    """Centro (0..1) do rosto a seguir em cada amostra, e os índices onde trocou de pessoa."""
    window = max(1, round(ACTIVITY_WINDOW * fps))
    need = max(1, round(SWITCH_SECONDS * fps))
    history: dict[int, list[float]] = {}

    def level(tid: int) -> float:
        h = history.get(tid, [])[-window:]
        return sum(h) / len(h) if h else 0.0

    # Pessoa inicial: a que mais mexe a boca durante a fala no primeiro 1,5 s (senão, o maior rosto).
    first = next((s for s in samples if s), {})
    early: dict[int, list[float]] = {}
    for s, sp in zip(samples[: round(1.5 * fps)], speech):
        for tid, f in s.items():
            if sp and f.activity is not None:
                early.setdefault(tid, []).append(f.activity)
    scores = {tid: sum(v) / len(v) for tid, v in early.items() if tid in first}
    current = (max(scores, key=scores.get) if scores and max(scores.values()) >= MIN_ACTIVITY
               else max(first, key=lambda t: first[t].w, default=None))

    out: list[float | None] = []
    switches: list[int] = []
    candidate, streak, lost = None, 0, 0
    hold = round(HOLD_SECONDS * fps)
    for i, (s, sp) in enumerate(zip(samples, speech)):
        for tid, f in s.items():
            if sp and f.activity is not None:
                history.setdefault(tid, []).append(f.activity)
        if not s:
            out.append(None)
            continue
        if current not in s:
            # O detector às vezes perde o rosto por um instante: segura a pessoa atual por até 1 s.
            lost += 1
            if lost <= hold:
                out.append(None)
                continue
            # Sumiu de vez: vai para quem mais fala entre os visíveis (ou o maior rosto).
            current = max(s, key=lambda t: (level(t), s[t].w))
            candidate, streak, lost = None, 0, 0
            switches.append(i)
        lost = 0
        if len(s) > 1 and sp:
            best = max(s, key=level)
            if (best != current and level(best) >= MIN_ACTIVITY
                    and level(best) > SWITCH_RATIO * level(current)):
                streak = streak + 1 if best == candidate else 1
                candidate = best
                if streak >= need:
                    current, candidate, streak = best, None, 0
                    switches.append(i)
            else:
                candidate, streak = None, 0
        out.append(s[current].cx)
    return out, switches


# --- suavização ---------------------------------------------------------------

def smooth_path(raw: list[float | None], fps: float = SAMPLE_FPS) -> FacePath:
    """Transforma os alvos (com falhas e ruído) num caminho estável para o recorte."""
    hold = round(HOLD_SECONDS * fps)
    outside_need = max(1, round(OUTSIDE_SECONDS * fps))
    max_step = MAX_SPEED / fps
    follow = 1 - (1 - FOLLOW_PER_SECOND) ** (1 / fps)
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
            pos, far, moving, outside = target, 0, False, 0   # troca de câmera/pessoa: pula direto
        else:
            outside = outside + 1 if abs(diff) > DEAD_ZONE else 0
            if outside >= outside_need:     # uma amostra isolada (falso positivo) não move o recorte
                moving = True
            elif abs(diff) < SETTLE:
                moving = False
            if moving:
                pos += max(-max_step, min(max_step, diff * follow))
        centers.append(pos)
    return FacePath(centers=centers, detected=sum(r is not None for r in raw))


def follow_path(samples: list[dict[int, Face]], words: Sequence[Word] | None, start: float) -> FacePath:
    """Da análise (rostos por amostra) até o caminho final do recorte."""
    speech = speech_mask(words, start, len(samples))
    targets, switch_idx = choose_targets(samples, speech)
    path = smooth_path(targets)
    path.people = len({tid for s in samples for tid in s})
    path.switches = [round(i / SAMPLE_FPS, 2) for i in switch_idx]
    return path


# --- recorte -------------------------------------------------------------------

def crop_box(info: VideoInfo) -> tuple[int, int]:
    """Largura e altura do recorte 9:16 no vídeo original (valores pares)."""
    ch = info.height
    cw = min(info.width, round(ch * 9 / 16))
    return cw - cw % 2, ch - ch % 2


def crop_positions(path: FacePath, info: VideoInfo, duration: float, out_fps: int = 30) -> list[int]:
    """x do recorte (pixels do vídeo original) para cada quadro de saída, interpolando as amostras.

    Nos saltos (troca de câmera/pessoa) não interpola: o corte é seco, como numa edição.
    """
    cw, _ = crop_box(info)
    max_x = info.width - cw
    c = path.centers or [0.5]
    n = max(1, round(duration * out_fps))
    xs = []
    for k in range(n):
        s = k / out_fps * SAMPLE_FPS            # posição em "amostras"
        i = min(int(s), len(c) - 1)
        j = min(i + 1, len(c) - 1)
        if i != j and abs(c[j] - c[i]) <= JUMP:
            center = c[i] + (c[j] - c[i]) * (s - int(s))
        else:
            center = c[i]
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
