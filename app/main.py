"""Servidor web do ClipForge (API + interface).

Uso: python -m app   (abre o navegador em http://127.0.0.1:8000)
"""
from __future__ import annotations

import errno
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import tempfile
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import db, doctor, tasks
from app.config import settings
from app.errors import ClipForgeError, NotFound
from app.jobs import jobs
from app.models import Clip, VideoInfo
from app.pipeline import captions
from app.pipeline.probe import probe
from app.pipeline.cut import SPEEDS
from app.pipeline.reframe import MODES, RESOLUTIONS
from app.pipeline.render import caption_words, export_full_captions
from app.pipeline.snap import snap_range
from app.pipeline.transcribe import WHISPER_MODELS, load_transcript
from app.projects import (Project, cleanup_leftovers, delete_project, delete_renders, dir_size, get_project,
                          open_project, register_existing)

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".webm", ".m4v", ".avi"}


def setup_logging() -> None:
    settings.logs_dir.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(settings.logs_dir / "clipforge.log", maxBytes=5_000_000,
                                                   backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpcore", "anthropic", "faster_whisper"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


@asynccontextmanager
async def lifespan(_: FastAPI):
    setup_logging()
    cleanup_leftovers()
    db.reset_interrupted_renders()
    register_existing()
    jobs.factory = tasks.build
    resumed = jobs.restore()
    if resumed:
        log.info("Fila retomada: %d tarefa(s)", resumed)
    app.state.problems = doctor.critical_problems()
    for p in app.state.problems:
        log.error("Problema no ambiente: %s", p)
    yield


app = FastAPI(title="ClipForge", lifespan=lifespan)


@app.exception_handler(ClipForgeError)
async def clipforge_error(_: Request, exc: ClipForgeError) -> JSONResponse:
    return JSONResponse({"detail": str(exc)}, status_code=404 if isinstance(exc, NotFound) else 400)


FIELD_NAMES = {
    "size": "tamanho", "position": "posição", "primary_color": "cor principal", "highlight_color": "cor de destaque",
    "outline_color": "cor do contorno", "box_color": "cor da caixa", "box_opacity": "opacidade da caixa",
    "outline": "contorno", "shadow": "sombra", "words_per_line": "palavras por linha", "max_chars": "caracteres por linha",
    "font": "fonte", "kind": "tipo", "name": "nome", "start": "início", "end": "fim", "file": "arquivo",
}


def _pt_error(err: dict) -> str:
    field = next((str(x) for x in reversed(err.get("loc", [])) if isinstance(x, str)), "")
    name = FIELD_NAMES.get(field, field)
    ctx, t = err.get("ctx") or {}, err.get("type", "")
    msg = {
        "missing": "é obrigatório",
        "greater_than_equal": f"precisa ser no mínimo {ctx.get('ge')}",
        "greater_than": f"precisa ser maior que {ctx.get('gt')}",
        "less_than_equal": f"precisa ser no máximo {ctx.get('le')}",
        "string_pattern_mismatch": "tem formato inválido (cores: #RRGGBB)",
        "string_too_short": "não pode ficar vazio",
        "string_too_long": f"pode ter no máximo {ctx.get('max_length')} caracteres",
        "literal_error": f"precisa ser uma destas opções: {str(ctx.get('expected')).replace(' or ', ' ou ')}",
        "int_parsing": "precisa ser um número inteiro",
        "float_parsing": "precisa ser um número",
    }.get(t, err.get("msg", "inválido"))
    return f"{name.capitalize()} {msg}." if name else f"Valor {msg}."


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse({"detail": " ".join(_pt_error(e) for e in exc.errors()[:3])}, status_code=422)


@app.exception_handler(Exception)
async def unexpected_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("Erro inesperado em %s %s", request.method, request.url.path)
    return JSONResponse({"detail": f"Erro inesperado ({type(exc).__name__}). "
                                   "Detalhes em data/logs/clipforge.log."}, status_code=500)


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
        "generated_bytes": dir_size(project.clips_dir) + dir_size(project.exports_dir),
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
        "render_speed": settings.render_speed,
        "whisper_models": list(WHISPER_MODELS),
        "language": settings.language,
        "clip_min_seconds": settings.clip_min_seconds,
        "clip_max_seconds": settings.clip_max_seconds,
        "fonts": list(captions.FONTS),
        "problems": getattr(app.state, "problems", []),
    }


@app.get("/api/presets")
def presets() -> dict:
    builtin = set(captions.builtin_presets())
    out = {}
    for name in captions.list_presets():
        try:
            out[name] = {**captions.load_preset(name).model_dump(), "builtin": name in builtin}
        except ClipForgeError as e:
            log.error("%s", e)  # estilo do usuário corrompido: ignora na lista
    return out


@app.post("/api/presets", status_code=201)
def create_preset(preset: captions.CaptionPreset) -> dict:
    key = captions.save_user_preset(preset)
    return {"key": key, **preset.model_dump(), "builtin": False}


@app.delete("/api/presets/{key}")
def remove_preset(key: str) -> dict:
    captions.delete_user_preset(key)
    return {"deleted": key}


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


@app.delete("/api/projects/{pid}")
def remove_project(pid: str) -> dict:
    """Apaga o projeto (o vídeo original no seu computador continua onde estava)."""
    get_project(pid)
    jobs.cancel_all(pid)
    if jobs.any_active(pid):
        raise ClipForgeError("Uma tarefa deste projeto ainda está terminando. Tente de novo em alguns segundos.")
    delete_project(pid)
    jobs.forget_project(pid)
    return {"deleted": pid}


@app.delete("/api/projects/{pid}/renders")
def remove_renders(pid: str) -> dict:
    """Apaga os vídeos e legendas gerados para liberar espaço (os cortes continuam na lista)."""
    project = get_project(pid)
    if jobs.active(pid, "render"):
        raise ClipForgeError("Há renders em andamento neste projeto. Cancele ou espere terminar.")
    return {"deleted_files": delete_renders(project)}


@app.post("/api/projects/{pid}/process")
def process(pid: str) -> dict:
    """Transcreve e sugere cortes (o que a tela de processamento dispara após o upload)."""
    project = get_project(pid)
    running = jobs.active(pid, "process") or jobs.active(pid, "transcribe")
    job = running or jobs.submit("process", pid)
    return job.public()


class TranscribeOptions(BaseModel):
    model: str | None = None      # modelo Whisper; padrão: WHISPER_MODEL do .env
    language: str | None = None   # pt, en, auto; padrão: LANGUAGE do .env
    force: bool = False           # refazer mesmo se já existir transcrição


@app.post("/api/projects/{pid}/transcribe")
def transcribe(pid: str, opts: TranscribeOptions | None = None) -> dict:
    get_project(pid)
    opts = opts or TranscribeOptions()
    params = opts.model_dump()
    tasks.transcribe_task(get_project(pid), opts.model, opts.language, opts.force)  # valida já, antes de enfileirar
    if jobs.active(pid, "render"):
        raise ClipForgeError("Há renders em andamento neste projeto. Espere terminar ou cancele antes de transcrever de novo.")
    job = jobs.active(pid, "transcribe") or jobs.active(pid, "process") or jobs.submit("transcribe", pid, params)
    return job.public()


@app.post("/api/projects/{pid}/suggest")
def suggest(pid: str) -> dict:
    project = get_project(pid)
    if not settings.anthropic_api_key:
        raise ClipForgeError("ANTHROPIC_API_KEY não está definida no .env.")
    if not project.transcript_path.exists():
        raise ClipForgeError("Transcreva o vídeo antes de pedir sugestões.")
    job = jobs.active(pid, "suggest") or jobs.submit("suggest", pid)
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
    return {"language": t.language, "duration": t.duration, "model": t.model, "words": words, "segments": segments,
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
    speed: str | None = None


def _check_render(opts: RenderOptions) -> None:
    if opts.style and opts.style not in captions.list_presets():
        raise ClipForgeError(f"Estilo de legenda '{opts.style}' não existe.")
    if opts.vertical and opts.vertical not in MODES:
        raise ClipForgeError(f"Modo vertical '{opts.vertical}' não existe.")
    if opts.resolution not in RESOLUTIONS:
        raise ClipForgeError(f"Resolução '{opts.resolution}' não existe.")
    if opts.speed and opts.speed not in SPEEDS:
        raise ClipForgeError(f"Velocidade '{opts.speed}' não existe. Use: {', '.join(SPEEDS)}.")


def _submit_render(project: Project, cid: str, opts: RenderOptions) -> dict:
    running = jobs.active(project.id, "render", cid)
    if running:
        return running.public()
    params = {"style": opts.style, "vertical": opts.vertical, "resolution": opts.resolution, "speed": opts.speed}
    return jobs.submit("render", project.id, params, clip_id=cid).public()


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


@app.get("/api/jobs")
def all_jobs() -> list[dict]:
    """Fila de todos os projetos (as ativas primeiro)."""
    names = {p["id"]: p["name"] for p in db.list_projects()}
    return [{**j.public(), "project_name": names.get(j.project_id, j.project_id)} for j in jobs.all()]


@app.post("/api/shutdown")
def shutdown(force: bool = False) -> dict:
    """Encerra o ClipForge. Com tarefas em andamento, só com force=true (elas são canceladas)."""
    active = jobs.any_active()
    if active and not force:
        raise ClipForgeError(f"Há {len(active)} tarefa(s) em andamento. Cancele ou confirme o encerramento.")

    def stop() -> None:
        jobs.cancel_all()
        os.kill(os.getpid(), signal.SIGINT)

    threading.Timer(0.5, stop).start()
    return {"stopping": True}


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
