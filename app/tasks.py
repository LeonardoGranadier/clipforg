"""Tarefas executadas pela fila (app.jobs) para a interface web."""
from __future__ import annotations

from app import db
from app.config import settings
from app.errors import Cancelled, ClipForgeError
from app.jobs import Job
from app.models import Clip, VideoInfo
from app.pipeline.reframe import is_vertical
from app.pipeline.render import render_clip
from app.pipeline.suggest import suggest_clips
from app.pipeline.transcribe import load_transcript, transcribe
from app.pipeline.transcribe import LANGUAGES, WHISPER_MODELS
from app.projects import Project, get_project


def project_info(project: Project) -> VideoInfo:
    row = db.get_project(project.id)
    if row is None:
        raise ClipForgeError(f"Projeto '{project.id}' não encontrado.")
    return VideoInfo.model_validate(row["info"])


def do_transcribe(project: Project, job: Job, weight: float = 1.0, model: str | None = None,
                  language: str | None = None, force: bool = False) -> None:
    job.set("Transcrevendo" + (f" ({model})" if model else ""), 0)
    transcribe(project.source, project.transcript_path, model_name=model, language=language, force=force,
               on_progress=lambda f: job.set(progress=f * weight), cancel=job.cancel_event)


def do_suggest(project: Project, job: Job, base: float = 0.0) -> None:
    """Pede sugestões ao Claude e substitui as sugestões antigas (cortes manuais são mantidos)."""
    job.set("Analisando com IA", base)
    transcript = load_transcript(project.transcript_path)
    new = suggest_clips(transcript, project.id, cancel=job.cancel_event,
                        on_progress=lambda f: job.set(progress=base + f * (1 - base)))
    manual = [c for c in project.load_clips() if not c.id.startswith("c")]
    project.save_clips(new + manual)
    job.result = {"suggested": len(new)}


def process_task(project: Project):
    """Depois do upload: transcreve e, se houver chave da API, sugere cortes."""
    def run(job: Job) -> None:
        do_transcribe(project, job, weight=0.85 if settings.anthropic_api_key else 1.0)
        if not settings.anthropic_api_key:
            job.message = "Sem ANTHROPIC_API_KEY no .env: crie os cortes manualmente."
            job.set("Pronto")
            return
        try:
            do_suggest(project, job, base=0.85)
        except Cancelled:
            raise
        except ClipForgeError as e:
            # A transcrição deu certo: o usuário ainda pode cortar manualmente.
            job.message = f"Sugestões da IA indisponíveis: {e}"
        job.set("Pronto")
    return run


def transcribe_task(project: Project, model: str | None = None, language: str | None = None,
                    force: bool = False):
    """Transcreve. Com `force`, refaz a transcrição (ex.: com um modelo mais preciso)."""
    if model is not None and model not in WHISPER_MODELS:
        raise ClipForgeError(f"Modelo Whisper '{model}' inválido. Use: {', '.join(WHISPER_MODELS)}.")
    if language is not None and language not in LANGUAGES:
        raise ClipForgeError(f"Idioma '{language}' inválido. Use: {', '.join(LANGUAGES)}.")

    def run(job: Job) -> None:
        do_transcribe(project, job, model=model, language=language, force=force)
        edits_reset = False
        if force and project.captions_edits_path.exists():
            # As correções apontam para posições de palavras da transcrição antiga: guardamos um backup.
            project.captions_edits_path.replace(project.captions_edits_path.with_suffix(".anterior.json"))
            edits_reset = True
        job.result = {"model": model or settings.whisper_model, "edits_reset": edits_reset}
        job.set("Pronto")
    return run


def suggest_task(project: Project):
    def run(job: Job) -> None:
        if not project.transcript_path.exists():
            raise ClipForgeError("Transcreva o vídeo antes de pedir sugestões.")
        do_suggest(project, job)
        job.set("Pronto")
    return run


def render_task(project: Project, clip_id: str, style: str | None, vertical: str | None, resolution: str,
                speed: str | None = None):
    def run(job: Job) -> None:
        clip = next((c for c in project.load_clips() if c.id == clip_id), None)
        if clip is None:
            raise ClipForgeError(f"O corte {clip_id} não existe mais.")
        if style and not project.transcript_path.exists():
            raise ClipForgeError("Transcreva o vídeo antes de gerar legendas.")
        transcript = load_transcript(project.transcript_path) if project.transcript_path.exists() else None
        info = project_info(project)
        applied = "fit" if vertical and is_vertical(info) else vertical  # vídeo vertical: sempre fit
        db.update_clip(project.id, clip_id, status="rendering")
        job.set("Renderizando", 0)
        try:
            out = render_clip(project, clip, transcript, info, style=style, vertical=applied,
                              resolution=resolution, speed=speed, on_progress=lambda f: job.set(progress=f),
                              cancel=job.cancel_event)
        except ClipForgeError:
            db.update_clip(project.id, clip_id, status="error" if not job.cancel_event.is_set() else "pending")
            raise
        db.update_clip(project.id, clip_id, status="done", output_path=str(out),
                       style=style, vertical_mode=applied)
        job.set("Pronto")
        job.result = {"output": out.name}
    return run


def build(job: Job):
    """Monta a função da tarefa a partir do que ficou salvo na fila (tipo + parâmetros)."""
    project = get_project(job.project_id)
    p = job.params
    if job.kind == "process":
        return process_task(project)
    if job.kind == "transcribe":
        return transcribe_task(project, p.get("model"), p.get("language"), bool(p.get("force")))
    if job.kind == "suggest":
        return suggest_task(project)
    if job.kind == "render":
        return render_task(project, job.clip_id, p.get("style"), p.get("vertical"),
                           p.get("resolution", "1080p"), p.get("speed"))
    raise ClipForgeError(f"Tipo de tarefa desconhecido: {job.kind}")


def clip_stale(old: Clip, new: Clip) -> bool:
    """Mudar início/fim invalida o vídeo já gerado."""
    return abs(old.start - new.start) > 1e-3 or abs(old.end - new.end) > 1e-3
