"""Fila de tarefas longas (transcrição, sugestão, render) com progresso e cancelamento.

Um único trabalhador executa as tarefas em ordem: transcrição e render já usam
todo o processador, rodar duas ao mesmo tempo só deixaria as duas mais lentas.
As tarefas ficam na memória: se o servidor reiniciar, a lista começa vazia.
"""
from __future__ import annotations

import itertools
import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app.errors import Cancelled, ClipForgeError

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "error", "cancelled"]


@dataclass
class Job:
    id: str
    kind: str
    project_id: str
    clip_id: str | None = None
    status: JobStatus = "queued"
    stage: str = "Na fila"
    progress: float = 0.0
    message: str = ""               # erro ou aviso para o usuário
    result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def set(self, stage: str | None = None, progress: float | None = None) -> None:
        if stage is not None:
            self.stage = stage
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))

    def public(self) -> dict:
        return {k: getattr(self, k) for k in
                ("id", "kind", "project_id", "clip_id", "status", "stage", "progress", "message", "result")}


TaskFn = Callable[[Job], None]


class JobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[tuple[Job, TaskFn]] = queue.Queue()
        self._lock = threading.Lock()
        self._ids = itertools.count(1)
        self._worker = threading.Thread(target=self._run, name="clipforge-worker", daemon=True)
        self._worker.start()

    def submit(self, kind: str, project_id: str, fn: TaskFn, clip_id: str | None = None) -> Job:
        with self._lock:
            job = Job(id=f"j{next(self._ids)}", kind=kind, project_id=project_id, clip_id=clip_id)
            self._jobs[job.id] = job
        self._queue.put((job, fn))
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def for_project(self, project_id: str) -> list[Job]:
        return [j for j in list(self._jobs.values()) if j.project_id == project_id]

    def active(self, project_id: str, kind: str | None = None, clip_id: str | None = None) -> Job | None:
        """Tarefa ainda não terminada (na fila ou rodando) com esses filtros."""
        for j in list(self._jobs.values()):
            if (j.project_id == project_id and j.status in ("queued", "running")
                    and (kind is None or j.kind == kind) and (clip_id is None or j.clip_id == clip_id)):
                return j
        return None

    def all(self, limit: int = 50) -> list[Job]:
        """Tarefas mais recentes primeiro (ativas sempre aparecem)."""
        jobs = sorted(list(self._jobs.values()), key=lambda j: j.created_at, reverse=True)
        active = [j for j in jobs if j.status in ("queued", "running")]
        done = [j for j in jobs if j.status not in ("queued", "running")]
        return active + done[: max(0, limit - len(active))]

    def any_active(self, project_id: str | None = None) -> list[Job]:
        return [j for j in list(self._jobs.values()) if j.status in ("queued", "running")
                and (project_id is None or j.project_id == project_id)]

    def cancel_all(self, project_id: str | None = None, timeout: float = 8) -> None:
        """Cancela tarefas (de um projeto ou todas) e espera as que estão rodando pararem."""
        for j in self.any_active(project_id):
            self.cancel(j.id)
        end = time.time() + timeout
        while time.time() < end and any(j.status == "running" for j in self.any_active(project_id)):
            time.sleep(0.1)

    def _prune(self, keep: int = 200) -> None:
        with self._lock:
            done = sorted((j for j in list(self._jobs.values()) if j.status not in ("queued", "running")),
                          key=lambda j: j.created_at)
            for j in done[: max(0, len(done) - keep)]:
                self._jobs.pop(j.id, None)

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job and job.status in ("queued", "running"):
            job.cancel_event.set()
            if job.status == "queued":
                job.status, job.stage = "cancelled", "Cancelado"
        return job

    def _run(self) -> None:
        while True:
            job, fn = self._queue.get()
            if job.cancel_event.is_set():
                continue
            job.status, job.stage = "running", "Iniciando"
            try:
                fn(job)
                job.status, job.progress = "done", 1.0
                if job.stage in ("Iniciando", ""):
                    job.stage = "Pronto"
            except Cancelled:
                job.status, job.stage = "cancelled", "Cancelado"
            except ClipForgeError as e:
                job.status, job.message = "error", str(e)
                log.error("Tarefa %s (%s) falhou: %s", job.id, job.kind, e)
            except Exception as e:  # erro inesperado: registra o traceback no log
                job.status, job.message = "error", f"Erro inesperado: {e}. Veja data/logs/clipforge.log."
                log.exception("Tarefa %s (%s) falhou", job.id, job.kind)
            finally:
                self._queue.task_done()
                self._prune()

    def wait_idle(self, timeout: float = 60) -> None:
        """Espera a fila esvaziar (usado nos testes)."""
        end = time.time() + timeout
        while time.time() < end:
            if all(j.status not in ("queued", "running") for j in list(self._jobs.values())):
                return
            time.sleep(0.05)
        raise TimeoutError("fila não terminou a tempo")


jobs = JobQueue()
