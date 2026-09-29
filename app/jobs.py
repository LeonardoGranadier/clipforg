"""Fila de tarefas longas (transcrição, sugestão, render) com progresso e cancelamento.

Um único trabalhador executa as tarefas em ordem: transcrição e render já usam
todo o processador, rodar duas ao mesmo tempo só deixaria as duas mais lentas.

A fila é salva no banco (tabela jobs): cada tarefa guarda o tipo e os parâmetros,
e a função que a executa é montada na hora (`factory`). Se o ClipForge for fechado,
ao abrir de novo as tarefas que estavam na fila ou rodando voltam para a fila
(as que rodavam começam do zero). Progresso e etapa ficam só na memória.
"""
from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

from app import db
from app.errors import Cancelled, ClipForgeError

log = logging.getLogger(__name__)

JobStatus = Literal["queued", "running", "done", "error", "cancelled"]
ACTIVE = ("queued", "running")


@dataclass
class Job:
    seq: int
    kind: str
    project_id: str
    clip_id: str | None = None
    params: dict = field(default_factory=dict)
    status: JobStatus = "queued"
    stage: str = "Na fila"
    progress: float = 0.0
    message: str = ""               # erro ou aviso para o usuário
    result: dict = field(default_factory=dict)
    created_at: float = field(default_factory=time.time)
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def id(self) -> str:
        return f"j{self.seq}"

    def set(self, stage: str | None = None, progress: float | None = None) -> None:
        if stage is not None:
            self.stage = stage
        if progress is not None:
            self.progress = max(0.0, min(1.0, progress))

    def public(self) -> dict:
        return {"id": self.id, **{k: getattr(self, k) for k in
                ("kind", "project_id", "clip_id", "status", "stage", "progress", "message", "result")}}


TaskFn = Callable[[Job], None]
# Monta a função da tarefa a partir do tipo e dos parâmetros (definida em app.tasks.build).
Factory = Callable[[Job], TaskFn]


class JobQueue:
    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._queue: queue.Queue[Job] = queue.Queue()
        self._lock = threading.Lock()
        self.factory: Factory | None = None
        self._worker = threading.Thread(target=self._run, name="clipforge-worker", daemon=True)
        self._worker.start()

    # --- persistência ---------------------------------------------------------

    def _save(self, job: Job) -> None:
        try:
            db.update_job(job.seq, job.status, job.message, job.result)
        except Exception:  # nunca derruba a fila por causa do banco
            log.exception("Não foi possível salvar o estado da tarefa %s", job.id)

    def restore(self) -> int:
        """Na inicialização: recarrega o histórico e põe de volta na fila o que não terminou."""
        resumed = 0
        for row in db.load_jobs():
            if f"j{row['seq']}" in self._jobs:
                continue
            job = Job(seq=row["seq"], kind=row["kind"], project_id=row["project_id"], clip_id=row["clip_id"],
                      params=row["params"], status=row["status"], message=row["message"],
                      result=row["result"], created_at=row["created_at"])
            if job.status in ACTIVE:
                job.stage = "Retomado após reinício" if job.status == "running" else "Na fila"
                job.status = "queued"
                self._save(job)
                resumed += 1
            else:
                job.stage = {"done": "Pronto", "error": "Erro", "cancelled": "Cancelado"}.get(job.status, "")
                job.progress = 1.0 if job.status == "done" else 0.0
            with self._lock:
                self._jobs[job.id] = job
            if job.status == "queued":
                self._queue.put(job)
        if resumed:
            log.info("%d tarefa(s) retomada(s) após reinício", resumed)
        return resumed

    # --- API -------------------------------------------------------------------

    def submit(self, kind: str, project_id: str, params: dict | None = None, clip_id: str | None = None) -> Job:
        params = params or {}
        created = time.time()
        seq = db.insert_job(kind, project_id, clip_id, params, created)
        job = Job(seq=seq, kind=kind, project_id=project_id, clip_id=clip_id, params=params, created_at=created)
        with self._lock:
            self._jobs[job.id] = job
        self._queue.put(job)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def for_project(self, project_id: str) -> list[Job]:
        return [j for j in list(self._jobs.values()) if j.project_id == project_id]

    def active(self, project_id: str, kind: str | None = None, clip_id: str | None = None) -> Job | None:
        """Tarefa ainda não terminada (na fila ou rodando) com esses filtros."""
        for j in list(self._jobs.values()):
            if (j.project_id == project_id and j.status in ACTIVE
                    and (kind is None or j.kind == kind) and (clip_id is None or j.clip_id == clip_id)):
                return j
        return None

    def all(self, limit: int = 50) -> list[Job]:
        """Tarefas mais recentes primeiro (ativas sempre aparecem)."""
        jobs = sorted(list(self._jobs.values()), key=lambda j: j.seq, reverse=True)
        active = [j for j in jobs if j.status in ACTIVE]
        done = [j for j in jobs if j.status not in ACTIVE]
        return active + done[: max(0, limit - len(active))]

    def any_active(self, project_id: str | None = None) -> list[Job]:
        return [j for j in list(self._jobs.values()) if j.status in ACTIVE
                and (project_id is None or j.project_id == project_id)]

    def cancel(self, job_id: str) -> Job | None:
        job = self._jobs.get(job_id)
        if job and job.status in ACTIVE:
            job.cancel_event.set()
            if job.status == "queued":
                job.status, job.stage = "cancelled", "Cancelado"
                self._save(job)
        return job

    def cancel_all(self, project_id: str | None = None, timeout: float = 8) -> None:
        """Cancela tarefas (de um projeto ou todas) e espera as que estão rodando pararem."""
        for j in self.any_active(project_id):
            self.cancel(j.id)
        end = time.time() + timeout
        while time.time() < end and any(j.status == "running" for j in self.any_active(project_id)):
            time.sleep(0.1)

    def forget_project(self, project_id: str) -> None:
        """Tira da memória as tarefas de um projeto apagado."""
        with self._lock:
            for jid in [jid for jid, j in self._jobs.items() if j.project_id == project_id]:
                self._jobs.pop(jid, None)

    def wait_idle(self, timeout: float = 60) -> None:
        """Espera a fila esvaziar (usado nos testes)."""
        end = time.time() + timeout
        while time.time() < end:
            if not self.any_active():
                return
            time.sleep(0.05)
        raise TimeoutError("fila não terminou a tempo")

    # --- trabalhador -----------------------------------------------------------

    def _prune(self, keep: int = 200) -> None:
        with self._lock:
            done = sorted((j for j in self._jobs.values() if j.status not in ACTIVE), key=lambda j: j.seq)
            for j in done[: max(0, len(done) - keep)]:
                self._jobs.pop(j.id, None)
        try:
            db.prune_jobs(keep)
        except Exception:
            log.exception("Não foi possível limpar o histórico de tarefas")

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job.cancel_event.is_set() or job.status != "queued" or job.id not in self._jobs:
                    continue
                job.status = "running"
                if job.stage != "Retomado após reinício":
                    job.stage = "Iniciando"
                self._save(job)
                try:
                    if self.factory is None:
                        raise ClipForgeError("Fila sem executor configurado.")
                    self.factory(job)(job)
                    job.status, job.progress = "done", 1.0
                    if job.stage in ("Iniciando", "Retomado após reinício", ""):
                        job.stage = "Pronto"
                except Cancelled:
                    job.status, job.stage = "cancelled", "Cancelado"
                except ClipForgeError as e:
                    job.status, job.message = "error", str(e)
                    log.error("Tarefa %s (%s) falhou: %s", job.id, job.kind, e)
                except Exception as e:  # erro inesperado: registra o traceback no log
                    job.status, job.message = "error", f"Erro inesperado: {e}. Veja data/logs/clipforge.log."
                    log.exception("Tarefa %s (%s) falhou", job.id, job.kind)
                self._save(job)
            finally:
                self._queue.task_done()
                self._prune()


jobs = JobQueue()
