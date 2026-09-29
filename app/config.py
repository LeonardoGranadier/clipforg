"""Configuração do ClipForge, lida do arquivo .env na raiz do projeto."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
# Pasta de dados; os testes apontam para uma pasta temporária.
DATA = Path(os.getenv("CLIPFORGE_DATA") or ROOT / "data")


def _int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    try:
        return int(raw) if raw else default
    except ValueError:
        raise ValueError(f"Valor inválido para {name} no .env: '{raw}' (esperado um número inteiro)")


@dataclass(frozen=True)
class Settings:
    root: Path = ROOT
    data_dir: Path = DATA
    projects_dir: Path = DATA / "projects"
    logs_dir: Path = DATA / "logs"
    fonts_dir: Path = ROOT / "fonts"

    anthropic_api_key: str = os.getenv("ANTHROPIC_API_KEY", "").strip()
    claude_model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-5").strip()

    whisper_model: str = os.getenv("WHISPER_MODEL", "small").strip()
    whisper_device: str = os.getenv("WHISPER_DEVICE", "cpu").strip().lower()
    language: str = os.getenv("LANGUAGE", "pt").strip().lower()

    clip_min_seconds: int = _int("CLIP_MIN_SECONDS", 15)
    clip_max_seconds: int = _int("CLIP_MAX_SECONDS", 90)


settings = Settings()
