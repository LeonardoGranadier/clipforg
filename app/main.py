"""Servidor web do ClipForge (API + interface).

Uso: python -m app   (abre o navegador em http://127.0.0.1:8000)
"""
from __future__ import annotations

import errno
import json
import logging
import re
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, tasks
from app.config import settings
from app.errors import ClipForgeError, NotFound
from app.jobs import jobs
from app.models import Clip, VideoInfo
from app.pipeline import captions
from app.pipeline.probe import probe
from app.pipeline.reframe import MODES, RESOLUTIONS
from app.pipeline.render import caption_words, export_full_captions
from app.pipeline.snap import snap_range
from app.pipeline.transcribe import load_transcript
from app.projects import Project, get_project, open_project, register_existing

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def setup_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(settings.logs_dir / "clipforge.log", encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    db.reset_interrupted_renders()
    register_existing()
    yield


app = FastAPI(title="ClipForge", lifespan=lifespan)


@app.exception_handler(ClipForgeError)
async def clipforge_error(_: Request, exc: ClipForgeError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404 if isinstance(exc, NotFound) else 400)


# --- helpers -----------------------------------------------------------------

def media_url(project: Project, path: Path | None) -> str | None:
    if not path or not path.exists():
        return None
    return f"/media/{project.id}/{path.relative_to(project.dir).as_posix()}"


def clip_out(project: Project, c: Clip) -> dict:
    d = c.model_dump()
    out = Path(c.output_path) if c.output_path else None
    d["duration"] = round(c.duration, 3)
    d["files"] = {
        "mp4": media_url(project, out),
        "srt": media_url(project, out.with_suffix(".srt")) if out and c.style else None,
        "ass": media_url(project, out.with_suffix(".ass")) if out and c.style else None,
    }
    job = jobs.active(project.id, "render", c.id)
    d["job"] = job.public() if job else None
    return d


def project_out(project: Project) -> dict:
    row = db.get_project(project.id)
    clips = project.load_clips()
    active = [j.public() for j in jobs.for_project(project.id) if j.status in ("queued", "running")]
    return {
        **row,
        "source_url": media_url(project, project.source),
        "has_transcript": project.transcript_path.exists(),
        "clips": len(clips),
        "rendered": sum(1 for c in clips if c.status == "done"),
        "jobs": active,
    }


def info_of(project: Project) -> VideoInfo:
    return tasks.project_info(project)


# --- status / presets ----------------------------------------------------------

@app.get("/api/status")
def status() -> dict:
    return {
        "api_key": bool(settings.anthropic_api_key),
        "claude_model": settings.claude_model,
        "whisper_model": settings.whisper_model,
        "vertical_modes": list(MODES),
        "resolutions": list(RESOLUTIONS),
        "clip_min_seconds": settings.clip_min_seconds,
        "clip_max_seconds": settings.clip_max_seconds,
    }


@app.get("/api/presets")
def presets() -> dict:
    return {name: captions.load_preset(name).model_dump() for name in captions.list_presets()}


# --- projetos ------------------------------------------------------------------

@app.get("/api/projects")
def list_projects() -> list[dict]:
    out = []
    for row in db.list_projects():
        try:
            out.append(project_out(get_project(row["id"])))
        except ClipForgeError:
            continue  # pasta do projeto apagada à mão
    return out


@app.post("/api/projects", status_code=201)
def upload(file: UploadFile = File(...)) -> dict:
    name = Path(file.filename or "video").name
    ext = Path(name).suffix.lower()
    if ext not in VIDEO_EXTS:
        raise ClipForgeError(f"Formato '{ext or '?'}' não suportado. Use: {', '.join(sorted(VIDEO_EXTS))}.")
    uploads = settings.data_dir / "uploads"
    uploads.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(dir=uploads))
    tmp = tmp_dir / name
    try:
        try:
            with open(tmp, "wb") as f:
                shutil.copyfileobj(file.file, f, length=4 * 1024 * 1024)
        except OSError as e:
            if e.errno == errno.ENOSPC:
                raise ClipForgeError("Disco cheio: não foi possível salvar o vídeo.") from e
            raise ClipForgeError(f"Não foi possível salvar o vídeo: {e}") from e
        info = probe(tmp)
        if not info.has_audio:
            raise ClipForgeError("O vídeo não tem áudio; o ClipForge precisa da fala para transcrever.")
        project = open_project(tmp, info, name=name)
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)  # o projeto já tem o próprio link/cópia
    return project_out(project)


@app.get("/api/projects/{pid}")
def project_detail(pid: str) -> dict:
    return project_out(get_project(pid))


@app.post("/api/projects/{pid}/process")
def process(pid: str) -> dict:
    """Transcreve e sugere cortes (o que a tela de processamento dispara após o upload)."""
    project = get_project(pid)
    running = jobs.active(pid, "process") or jobs.active(pid, "transcribe")
    job = running or jobs.submit("process", pid, tasks.process_task(project))
    return job.public()


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str) -> dict:
    project = get_project(pid)
    job = jobs.active(pid, "transcribe") or jobs.submit("transcribe", pid, tasks.transcribe_task(project))
    return job.public()


@app.post("/api/projects/{pid}/suggest")
def suggest(pid: str) -> dict:
    project = get_project(pid)
    if not settings.anthropic_api_key:
        raise ClipForgeError("ANTHROPIC_API_KEY não está definida no .env.")
    job = jobs.active(pid, "suggest") or jobs.submit("suggest", pid, tasks.suggest_task(project))
    return job.public()


# --- transcrição e legendas ----------------------------------------------------

@app.get("/api/projects/{pid}/transcript")
def transcript(pid: str) -> dict:
    project = get_project(pid)
    if not project.transcript_path.exists():
        raise NotFound("O vídeo ainda não foi transcrito.")
    t = load_transcript(project.transcript_path)
    words, segments, i = [], [], 0
    for s in t.segments:
        first = i
        for w in s.words:
            words.append({"i": i, "word": w.word, "start": w.start, "end": w.end, "prob": w.prob})
            i += 1
        segments.append({"start": s.start, "end": s.end, "first": first, "last": i - 1})
    return {"language": t.language, "duration": t.duration, "words": words, "segments": segments,
            "edits": captions.load_edits(project.captions_edits_path)}


class CaptionEdits(BaseModel):
    edits: dict[int, str]


@app.put("/api/projects/{pid}/captions")
def save_captions(pid: str, body: CaptionEdits) -> dict:
    project = get_project(pid)
    if not project.transcript_path.exists():
        raise ClipForgeError("O vídeo ainda não foi transcrito.")
    n_words = len(load_transcript(project.transcript_path).all_words())
    bad = [k for k in body.edits if not 0 <= k < n_words]
    if bad:
        raise ClipForgeError(f"Palavras inexistentes: {bad[:5]}")
    project.captions_edits_path.write_text(
        json.dumps({str(k): v for k, v in sorted(body.edits.items())}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    return {"saved": len(body.edits)}


@app.get("/api/projects/{pid}/captions/cues")
def caption_cues(pid: str, style: str = "classic") -> dict:
    """Blocos de legenda do vídeo inteiro (tempos absolutos) para a prévia ao vivo."""
    project = get_project(pid)
    if not project.transcript_path.exists():
        raise NotFound("O vídeo ainda não foi transcrito.")
    preset = captions.load_preset(style)
    cues = captions.cues_for_preset(caption_words(project, load_transcript(project.transcript_path)), preset)
    return {"preset": preset.model_dump(), "cues": [
        {"start": c.start, "end": c.end, "words": [{"word": w.word, "start": w.start, "end": w.end} for w in c.words]}
        for c in cues]}


class ExportCaptions(BaseModel):
    style: str = "classic"


@app.post("/api/projects/{pid}/captions/export")
def export_captions(pid: str, body: ExportCaptions) -> dict:
    """Modo só legendas: .srt e .ass do vídeo inteiro."""
    project = get_project(pid)
    if not project.transcript_path.exists():
        raise ClipForgeError("O vídeo ainda não foi transcrito.")
    ass_path, srt_path = export_full_captions(project, load_transcript(project.transcript_path),
                                              info_of(project), body.style)
    return {"srt": media_url(project, srt_path), "ass": media_url(project, ass_path)}


# --- cortes ----------------------------------------------------------------------

class ClipIn(BaseModel):
    id: str | None = None
    start: float = Field(ge=0)
    end: float = Field(gt=0)
    title: str = ""
    hook: str = ""
    reason: str = ""
    score: int = 0


@app.get("/api/projects/{pid}/clips")
def get_clips(pid: str) -> list[dict]:
    project = get_project(pid)
    return [clip_out(project, c) for c in project.load_clips()]


@app.put("/api/projects/{pid}/clips")
def put_clips(pid: str, body: list[ClipIn]) -> list[dict]:
    """Salva a lista inteira (ordem, início/fim, títulos). Cortes fora da lista são apagados."""
    project = get_project(pid)
    duration = info_of(project).duration
    old = {c.id: c for c in project.load_clips()}
    used = set(old) | {c.id for c in body if c.id}
    n = 1
    out: list[Clip] = []
    for ci in body:
        if ci.end <= ci.start:
            raise ClipForgeError(f"Corte '{ci.title or ci.id}': o fim precisa ser depois do início.")
        start, end = round(ci.start, 3), round(min(ci.end, duration), 3)
        cid = ci.id if ci.id and re.fullmatch(r"[a-z0-9]{1,12}", ci.id) else None
        if cid is None:
            while f"m{n:02d}" in used:
                n += 1
            cid = f"m{n:02d}"
            used.add(cid)
        prev = old.get(cid)
        clip = Clip(id=cid, project_id=pid, start=start, end=end,
                    title=ci.title.strip() or f"Corte {cid}", hook=ci.hook, reason=ci.reason, score=ci.score)
        if prev and not tasks.clip_stale(prev, clip):
            clip.status, clip.output_path = prev.status, prev.output_path
            clip.style, clip.vertical_mode = prev.style, prev.vertical_mode
        out.append(clip)
    ids = [c.id for c in out]
    if len(ids) != len(set(ids)):
        raise ClipForgeError("Há cortes com o mesmo id na lista.")
    project.save_clips(out)
    return [clip_out(project, c) for c in out]


class Range(BaseModel):
    start: float
    end: float


@app.post("/api/projects/{pid}/snap")
def snap(pid: str, body: Range) -> dict:
    """Encaixa início/fim nas palavras mais próximas (sem cortar palavra no meio)."""
    project = get_project(pid)
    if not project.transcript_path.exists():
        raise ClipForgeError("O vídeo ainda não foi transcrito.")
    t = load_transcript(project.transcript_path)
    start, end = snap_range(body.start, body.end, t.all_words(), info_of(project).duration)
    return {"start": start, "end": end}


class RenderOptions(BaseModel):
    style: str | None = None
    vertical: str | None = None
    resolution: str = "1080p"


def _check_render(opts: RenderOptions) -> None:
    if opts.style and opts.style not in captions.list_presets():
        raise ClipForgeError(f"Estilo de legenda '{opts.style}' não existe.")
    if opts.vertical and opts.vertical not in MODES:
        raise ClipForgeError(f"Modo vertical '{opts.vertical}' não existe.")
    if opts.resolution not in RESOLUTIONS:
        raise ClipForgeError(f"Resolução '{opts.resolution}' não existe.")


def _submit_render(project: Project, cid: str, opts: RenderOptions) -> dict:
    running = jobs.active(project.id, "render", cid)
    if running:
        return running.public()
    return jobs.submit("render", project.id, tasks.render_task(project, cid, opts.style, opts.vertical,
                                                              opts.resolution), clip_id=cid).public()


@app.post("/api/projects/{pid}/clips/{cid}/render")
def render_one(pid: str, cid: str, opts: RenderOptions) -> dict:
    project = get_project(pid)
    _check_render(opts)
    if cid not in {c.id for c in project.load_clips()}:
        raise NotFound(f"Corte '{cid}' não encontrado.")
    return _submit_render(project, cid, opts)


@app.post("/api/projects/{pid}/render-all")
def render_all(pid: str, opts: RenderOptions) -> list[dict]:
    project = get_project(pid)
    _check_render(opts)
    return [_submit_render(project, c.id, opts) for c in project.load_clips()]


# --- tarefas -----------------------------------------------------------------------

@app.get("/api/projects/{pid}/jobs")
def project_jobs(pid: str) -> list[dict]:
    get_project(pid)
    return [j.public() for j in jobs.for_project(pid)]


@app.get("/api/jobs/{job_id}")
def job_status(job_id: str) -> dict:
    job = jobs.get(job_id)
    if not job:
        raise NotFound("Tarefa não encontrada (o servidor pode ter sido reiniciado).")
    return job.public()


@app.post("/api/jobs/{job_id}/cancel")
def job_cancel(job_id: str) -> dict:
    job = jobs.cancel(job_id)
    if not job:
        raise NotFound("Tarefa não encontrada.")
    return job.public()


# --- arquivos ------------------------------------------------------------------------

@app.get("/media/{pid}/{path:path}")
def media(pid: str, path: str, download: bool = False) -> FileResponse:
    """Serve vídeos e legendas do projeto (com suporte a Range, para o player)."""
    project = get_project(pid)
    target = (project.dir / path).resolve()
    allowed = [(project.dir / d).resolve() for d in ("source", "clips", "exports")]
    if not any(target.is_relative_to(a) for a in allowed) or not target.is_file():
        raise HTTPException(404, "Arquivo não encontrado.")
    return FileResponse(target, filename=target.name if download else None)


app.mount("/fonts", StaticFiles(directory=settings.fonts_dir), name="fonts")
app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
