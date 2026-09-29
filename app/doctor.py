"""Verifica se o ambiente está pronto para o ClipForge.

Uso: python -m app.doctor
"""
from __future__ import annotations

import shutil
import subprocess
import sys
from dataclasses import dataclass

from app.config import settings

OK, WARN, FAIL = "OK", "AVISO", "FALTA"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def _run(cmd: list[str], timeout: int = 15) -> subprocess.CompletedProcess | None:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None


def check_python() -> Check:
    v = sys.version_info
    ver = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) < (3, 11):
        return Check("Python", FAIL, f"versão {ver}; é preciso 3.11 ou mais nova")
    return Check("Python", OK, ver)


def check_binary(name: str) -> Check:
    path = shutil.which(name)
    if not path:
        return Check(name, FAIL, f"'{name}' não encontrado. Instale o FFmpeg (veja o README).")
    res = _run([name, "-version"])
    first = res.stdout.splitlines()[0] if res and res.stdout else "versão desconhecida"
    return Check(name, OK, first)


def check_encoders() -> Check:
    res = _run(["ffmpeg", "-hide_banner", "-encoders"])
    if not res or res.returncode != 0:
        return Check("Codificadores", FAIL, "não foi possível listar os codificadores do FFmpeg")
    out = res.stdout
    if "libx264" not in out:
        return Check("Codificadores", FAIL, "FFmpeg sem libx264; instale uma versão completa do FFmpeg")
    hw = [e for e in ("h264_nvenc", "h264_videotoolbox") if e in out]
    extra = f"; aceleração listada: {', '.join(hw)} (só usada se o teste real funcionar)" if hw else ""
    return Check("Codificadores", OK, f"libx264 disponível{extra}")


def check_ass_filter() -> Check:
    res = _run(["ffmpeg", "-hide_banner", "-filters"])
    if res and " ass " in res.stdout:
        return Check("Filtro de legendas", OK, "filtro 'ass' (libass) disponível")
    return Check("Filtro de legendas", FAIL, "FFmpeg sem libass; as legendas não poderão ser gravadas no vídeo")


def check_nvenc() -> Check:
    """Testa de verdade o h264_nvenc codificando 1 segundo de vídeo sintético."""
    if not shutil.which("nvidia-smi"):
        return Check("NVENC (GPU)", WARN, "sem GPU NVIDIA; será usado libx264 (CPU)")
    res = _run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
        "-i", "testsrc=size=640x360:rate=30:duration=1",
        "-c:v", "h264_nvenc", "-f", "null", "-",
    ], timeout=30)
    if res and res.returncode == 0:
        return Check("NVENC (GPU)", OK, "h264_nvenc funciona; pode ser usado para renderizar mais rápido")
    return Check("NVENC (GPU)", WARN, "h264_nvenc não funciona nesta placa; será usado libx264 (CPU)")


def check_cuda() -> Check:
    try:
        import ctranslate2
    except ImportError:
        return Check("CUDA (Whisper)", FAIL, "ctranslate2 não instalado; rode: uv pip install -r requirements.txt")
    try:
        n = ctranslate2.get_cuda_device_count()
    except Exception:
        n = 0
    if n == 0:
        return Check("CUDA (Whisper)", WARN, "nenhuma GPU utilizável; a transcrição roda no processador (int8)")
    types = ctranslate2.get_supported_compute_types("cuda")
    return Check("CUDA (Whisper)", OK if "float16" in types else WARN,
                 f"{n} GPU(s); tipos suportados: {', '.join(sorted(types))}. "
                 f"Configurado: WHISPER_DEVICE={settings.whisper_device}")


def check_python_packages() -> Check:
    missing = []
    for mod in ("fastapi", "uvicorn", "pydantic", "dotenv", "faster_whisper", "anthropic", "cv2", "python_multipart"):
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if missing:
        return Check("Pacotes Python", FAIL, f"faltando: {', '.join(missing)}. Rode: uv pip install -r requirements.txt")
    return Check("Pacotes Python", OK, "todos instalados")


def check_api_key() -> Check:
    if not (settings.root / ".env").exists():
        return Check("Chave da API", WARN, "arquivo .env não existe; copie o .env.example para .env")
    key = settings.anthropic_api_key
    if not key:
        return Check("Chave da API", WARN, "ANTHROPIC_API_KEY vazia; sugestões por IA ficam desligadas (cortes manuais funcionam)")
    if not key.startswith("sk-ant-"):
        return Check("Chave da API", WARN, "ANTHROPIC_API_KEY não parece uma chave da Anthropic (deveria começar com 'sk-ant-')")
    # Mostra só o final da chave, nunca ela inteira.
    return Check("Chave da API", OK, f"definida (...{key[-4:]}); modelo: {settings.claude_model}")


def check_fonts() -> Check:
    fonts = sorted(p.name for p in settings.fonts_dir.glob("*.ttf"))
    if not fonts:
        return Check("Fontes", FAIL, f"nenhuma fonte .ttf em {settings.fonts_dir}")
    return Check("Fontes", OK, ", ".join(fonts))


def check_dirs() -> Check:
    for d in (settings.projects_dir, settings.logs_dir):
        d.mkdir(parents=True, exist_ok=True)
    free_gb = shutil.disk_usage(settings.data_dir).free / 1024**3
    status = OK if free_gb >= 5 else WARN
    return Check("Pastas de dados", status, f"{settings.data_dir} ({free_gb:.0f} GB livres)")


def check_face_model() -> Check:
    model = settings.root / "models" / "face_detection_yunet_2023mar.onnx"
    if not model.exists():
        return Check("Modelo de rosto", FAIL, f"arquivo não encontrado: {model} (o modo 'seguir o rosto' não funciona)")
    return Check("Modelo de rosto", OK, model.name)


def critical_problems() -> list[str]:
    """Verificações rápidas usadas pela interface ao iniciar (sem os testes lentos de GPU)."""
    checks = [check_binary("ffmpeg"), check_binary("ffprobe"), check_ass_filter(), check_fonts(), check_face_model()]
    return [f"{c.name}: {c.detail}" for c in checks if c.status == FAIL]


def run_all() -> list[Check]:
    return [
        check_python(),
        check_python_packages(),
        check_binary("ffmpeg"),
        check_binary("ffprobe"),
        check_encoders(),
        check_ass_filter(),
        check_nvenc(),
        check_cuda(),
        check_api_key(),
        check_fonts(),
        check_face_model(),
        check_dirs(),
    ]


def main() -> int:
    print("ClipForge doctor: verificando o ambiente...\n")
    checks = run_all()
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status:^5}] {c.name:<{width}}  {c.detail}")
    fails = [c for c in checks if c.status == FAIL]
    warns = [c for c in checks if c.status == WARN]
    print()
    if fails:
        print(f"{len(fails)} item(ns) faltando. Corrija os marcados como FALTA antes de continuar.")
        return 1
    print("Tudo pronto." + (f" ({len(warns)} aviso(s), não impedem o uso.)" if warns else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
