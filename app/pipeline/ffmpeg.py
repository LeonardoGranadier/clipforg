"""Execução do FFmpeg/FFprobe com progresso, log e mensagens de erro claras."""
from __future__ import annotations

import shutil
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path

from app.errors import Cancelled, ClipForgeError

ProgressFn = Callable[[float], None]


def require(binary: str) -> str:
    path = shutil.which(binary)
    if not path:
        raise ClipForgeError(
            f"'{binary}' não foi encontrado. Instale o FFmpeg (veja o README) e rode 'python -m app.doctor'."
        )
    return path


def explain_failure(stderr: str) -> str:
    """Traduz os erros mais comuns do FFmpeg para uma mensagem compreensível."""
    s = stderr.lower()
    if "no space left on device" in s:
        return "Disco cheio: libere espaço e tente de novo."
    if "invalid data found" in s or "moov atom not found" in s:
        return "O arquivo de vídeo parece corrompido ou num formato não suportado."
    if "no such file or directory" in s:
        return "Arquivo não encontrado."
    if "permission denied" in s:
        return "Sem permissão para ler ou gravar o arquivo."
    last = [line for line in stderr.strip().splitlines() if line.strip()][-3:]
    return "O FFmpeg falhou: " + " | ".join(last) if last else "O FFmpeg falhou sem mensagem."


def run_ffmpeg(
    args: list[str],
    *,
    log_path: Path,
    duration: float | None = None,
    on_progress: ProgressFn | None = None,
    cancel: threading.Event | None = None,
) -> None:
    """Roda `ffmpeg <args>`. O stderr vai para `log_path`; o progresso (0..1) vem de `-progress`.

    Se `cancel` for acionado, o FFmpeg é encerrado e `Cancelled` é lançado.
    """
    require("ffmpeg")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    cmd = ["ffmpeg", "-hide_banner", "-nostdin", "-nostats", "-progress", "pipe:1", *args]
    with open(log_path, "w", encoding="utf-8") as log:
        log.write(" ".join(cmd) + "\n\n")
        log.flush()
        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=log, text=True)
        except OSError as e:
            raise ClipForgeError(f"Não foi possível iniciar o FFmpeg: {e}") from e
        assert proc.stdout is not None
        for line in proc.stdout:
            if cancel is not None and cancel.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    proc.kill()
                raise Cancelled()
            # out_time_us está em microssegundos (out_time_ms também, por um bug histórico)
            if on_progress and duration and line.startswith("out_time_us="):
                try:
                    t = int(line.split("=", 1)[1]) / 1_000_000
                except ValueError:
                    continue
                on_progress(max(0.0, min(1.0, t / duration)))
        code = proc.wait()
    if code != 0:
        stderr = log_path.read_text(encoding="utf-8", errors="replace")
        raise ClipForgeError(f"{explain_failure(stderr)} (detalhes em {log_path})")
    if on_progress:
        on_progress(1.0)


def filter_path(path: Path) -> str:
    """Escapa um caminho para usar como valor de opção de filtro (ex.: ass=filename=...).

    O FFmpeg tem dois níveis de escape: o do valor da opção (\\ ' :) e o do grafo
    de filtros (\\ ' [ ] , ;). No Windows, as barras viram '/' antes (C:/pasta).
    Use o resultado SEM aspas em volta.
    """
    s = str(path.resolve()).replace("\\", "/")
    for ch in "\\':":           # nível 1: valor da opção
        s = s.replace(ch, "\\" + ch)
    for ch in "\\'[],;":        # nível 2: grafo de filtros
        s = s.replace(ch, "\\" + ch)
    return s
