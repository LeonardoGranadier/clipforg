"""Pastas e arquivos de um projeto em data/projects/<id>/."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from app.config import settings
from app.errors import ClipForgeError
from app.models import Clip

_CHUNK = 1024 * 1024


def slugify(text: str, max_len: int = 40) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    return text[:max_len].strip("-") or "video"


def fingerprint(path: Path) -> str:
    """Identifica o arquivo pelo tamanho + primeiro e último MB (rápido mesmo em vídeos grandes)."""
    h = hashlib.sha1()
    size = path.stat().st_size
    h.update(str(size).encode())
    with open(path, "rb") as f:
        h.update(f.read(_CHUNK))
        if size > _CHUNK:
            f.seek(max(0, size - _CHUNK))
            h.update(f.read(_CHUNK))
    return h.hexdigest()[:8]


@dataclass(frozen=True)
class Project:
    id: str
    dir: Path

    @property
    def source_dir(self) -> Path:
        return self.dir / "source"

    @property
    def source(self) -> Path:
        files = sorted(self.source_dir.glob("*"))
        if not files:
            raise ClipForgeError(f"Projeto {self.id} sem vídeo de origem.")
        return files[0]

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def clips_path(self) -> Path:
        return self.dir / "clips.json"

    @property
    def captions_edits_path(self) -> Path:
        return self.dir / "captions_edited.json"

    @property
    def clips_dir(self) -> Path:
        return self.dir / "clips"

    @property
    def exports_dir(self) -> Path:
        return self.dir / "exports"

    def load_clips(self) -> list[Clip]:
        if not self.clips_path.exists():
            return []
        data = json.loads(self.clips_path.read_text(encoding="utf-8"))
        return [Clip.model_validate(c) for c in data]

    def save_clips(self, clips: list[Clip]) -> None:
        tmp = self.clips_path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps([c.model_dump() for c in clips], ensure_ascii=False, indent=2), encoding="utf-8"
        )
        tmp.replace(self.clips_path)


def open_project(video: Path) -> Project:
    """Cria (ou reabre, se o mesmo vídeo já foi processado) o projeto de um vídeo."""
    video = video.resolve()
    pid = f"{slugify(video.stem)}-{fingerprint(video)}"
    project = Project(pid, settings.projects_dir / pid)
    for d in (project.source_dir, project.clips_dir, project.exports_dir):
        d.mkdir(parents=True, exist_ok=True)

    if not any(project.source_dir.iterdir()):
        dest = project.source_dir / f"{slugify(video.stem)}{video.suffix.lower()}"
        try:
            os.link(video, dest)  # sem cópia quando está no mesmo disco
        except OSError:
            try:
                shutil.copy2(video, dest)
            except OSError as e:
                raise ClipForgeError(f"Não foi possível copiar o vídeo para o projeto: {e}") from e
    return project
