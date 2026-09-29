"""Banco SQLite com projetos e cortes (data/clipforge.db).

Transcrições continuam em JSON (transcript.json), porque são grandes e só leitura.
"""
from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from app.config import settings
from app.models import Clip, VideoInfo

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  REAL NOT NULL,
    info        TEXT NOT NULL          -- VideoInfo em JSON
);
CREATE TABLE IF NOT EXISTS clips (
    project_id    TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    id            TEXT NOT NULL,
    position      INTEGER NOT NULL,
    start         REAL NOT NULL,
    "end"         REAL NOT NULL,
    title         TEXT NOT NULL,
    hook          TEXT NOT NULL DEFAULT '',
    reason        TEXT NOT NULL DEFAULT '',
    score         INTEGER NOT NULL DEFAULT 0,
    style         TEXT,
    vertical_mode TEXT,
    status        TEXT NOT NULL DEFAULT 'pending',
    output_path   TEXT,
    PRIMARY KEY (project_id, id)
);
CREATE TABLE IF NOT EXISTS jobs (
    seq         INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    project_id  TEXT NOT NULL,
    clip_id     TEXT,
    params      TEXT NOT NULL DEFAULT '{}',
    status      TEXT NOT NULL,
    message     TEXT NOT NULL DEFAULT '',
    result      TEXT NOT NULL DEFAULT '{}',
    created_at  REAL NOT NULL
);
"""

CLIP_FIELDS = ("id", "project_id", "start", "end", "title", "hook", "reason", "score",
               "style", "vertical_mode", "status", "output_path")


def db_path() -> Path:
    return settings.data_dir / "clipforge.db"


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    """Uma conexão por operação: seguro entre as threads do servidor e das tarefas."""
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# --- projetos --------------------------------------------------------------

def upsert_project(pid: str, name: str, info: VideoInfo) -> None:
    with connect() as c:
        c.execute(
            "INSERT INTO projects (id, name, created_at, info) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET info = excluded.info",
            (pid, name, time.time(), info.model_dump_json()),
        )


def get_project(pid: str) -> dict | None:
    with connect() as c:
        row = c.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone()
    return _project_row(row) if row else None


def list_projects() -> list[dict]:
    with connect() as c:
        rows = c.execute("SELECT * FROM projects ORDER BY created_at DESC").fetchall()
    return [_project_row(r) for r in rows]


def _project_row(row: sqlite3.Row) -> dict:
    return {"id": row["id"], "name": row["name"], "created_at": row["created_at"],
            "info": json.loads(row["info"])}


# --- cortes ----------------------------------------------------------------

def load_clips(pid: str) -> list[Clip]:
    with connect() as c:
        rows = c.execute("SELECT * FROM clips WHERE project_id = ? ORDER BY position", (pid,)).fetchall()
    return [Clip.model_validate({k: r[k] for k in CLIP_FIELDS}) for r in rows]


def save_clips(pid: str, clips: list[Clip]) -> None:
    """Substitui a lista de cortes do projeto (a ordem da lista é a ordem de exibição)."""
    with connect() as c:
        c.execute("DELETE FROM clips WHERE project_id = ?", (pid,))
        c.executemany(
            f"INSERT INTO clips (position, {', '.join(_q(f) for f in CLIP_FIELDS)}) "
            f"VALUES (?, {', '.join('?' for _ in CLIP_FIELDS)})",
            [(i, *(getattr(cl, f) if f != "project_id" else pid for f in CLIP_FIELDS))
             for i, cl in enumerate(clips)],
        )


def update_clip(pid: str, cid: str, **fields) -> None:
    """Atualiza só alguns campos de um corte (usado pelas tarefas de render)."""
    bad = set(fields) - set(CLIP_FIELDS)
    if bad:
        raise ValueError(f"campos inválidos: {bad}")
    sets = ", ".join(f"{_q(k)} = ?" for k in fields)
    with connect() as c:
        c.execute(f"UPDATE clips SET {sets} WHERE project_id = ? AND id = ?", (*fields.values(), pid, cid))


def _q(name: str) -> str:
    return f'"{name}"'


def reset_interrupted_renders() -> None:
    """Na inicialização: renders interrompidos (servidor fechado no meio) voltam a 'pending'."""
    with connect() as c:
        c.execute("UPDATE clips SET status = 'pending' WHERE status = 'rendering'")


def delete_project(pid: str) -> None:
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE project_id = ?", (pid,))
        c.execute("DELETE FROM clips WHERE project_id = ?", (pid,))
        c.execute("DELETE FROM projects WHERE id = ?", (pid,))


# --- fila de tarefas (sobrevive a reinícios) -----------------------------------

def insert_job(kind: str, project_id: str, clip_id: str | None, params: dict, created_at: float) -> int:
    with connect() as c:
        cur = c.execute(
            "INSERT INTO jobs (kind, project_id, clip_id, params, status, created_at) VALUES (?, ?, ?, ?, 'queued', ?)",
            (kind, project_id, clip_id, json.dumps(params), created_at),
        )
        return int(cur.lastrowid)


def update_job(seq: int, status: str, message: str = "", result: dict | None = None) -> None:
    with connect() as c:
        c.execute("UPDATE jobs SET status = ?, message = ?, result = ? WHERE seq = ?",
                  (status, message, json.dumps(result or {}), seq))


def load_jobs(limit: int = 200) -> list[dict]:
    """Tarefas não terminadas (todas) + as últimas terminadas, da mais antiga para a mais nova."""
    with connect() as c:
        rows = c.execute(
            "SELECT * FROM jobs WHERE status IN ('queued', 'running') "
            "OR seq IN (SELECT seq FROM jobs WHERE status NOT IN ('queued', 'running') ORDER BY seq DESC LIMIT ?) "
            "ORDER BY seq", (limit,)).fetchall()
    return [{**dict(r), "params": json.loads(r["params"]), "result": json.loads(r["result"])} for r in rows]


def prune_jobs(keep: int = 200) -> None:
    with connect() as c:
        c.execute("DELETE FROM jobs WHERE status NOT IN ('queued', 'running') AND seq NOT IN "
                  "(SELECT seq FROM jobs WHERE status NOT IN ('queued', 'running') ORDER BY seq DESC LIMIT ?)", (keep,))
