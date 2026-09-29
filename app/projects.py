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

from app import db
from app.config import settings
from app.errors import ClipForgeError, NotFound
from app.models import Clip, VideoInfo

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
        return db.load_clips(self.id)

    def save_clips(self, clips: list[Clip]) -> None:
        db.save_clips(self.id, clips)

    def _import_legacy_clips(self) -> None:
        """Projetos da Fase 1-3 guardavam os cortes em clips.json: importa uma vez para o banco."""
        if not self.clips_path.exists():
            return
        if not db.load_clips(self.id):
            data = json.loads(self.clips_path.read_text(encoding="utf-8"))
            db.save_clips(self.id, [Clip.model_validate(c) for c in data])
        self.clips_path.rename(self.clips_path.with_suffix(".json.importado"))


def get_project(pid: str) -> Project:
    if not re.fullmatch(r"[a-z0-9-]+", pid) or db.get_project(pid) is None:
        raise NotFound(f"Projeto '{pid}' não encontrado.")
    return Project(pid, settings.projects_dir / pid)


def open_project(video: Path, info: VideoInfo, name: str | None = None) -> Project:
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
    db.upsert_project(pid, name or video.name, info)
    project._import_legacy_clips()
    return project


def register_existing() -> int:
    """Registra no banco projetos criados antes dele existir (pastas em data/projects/)."""
    from app.pipeline.probe import probe  # import local: evita ciclo

    n = 0
    if not settings.projects_dir.exists():
        return 0
    for d in sorted(settings.projects_dir.iterdir()):
        if not d.is_dir() or db.get_project(d.name) is not None:
            continue
        project = Project(d.name, d)
        try:
            source = project.source
            db.upsert_project(d.name, source.name, probe(source))
        except ClipForgeError:
            continue
        project._import_legacy_clips()
        n += 1
    return n
