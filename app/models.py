"""Modelos de dados do ClipForge."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class Word(BaseModel):
    word: str
    start: float
    end: float
    prob: float | None = None


class Segment(BaseModel):
    id: int
    start: float
    end: float
    text: str
    words: list[Word]


class Transcript(BaseModel):
    language: str
    duration: float
    segments: list[Segment]
    model: str | None = None   # modelo Whisper usado (transcrições antigas não têm)

    def all_words(self) -> list[Word]:
        return [w for s in self.segments for w in s.words]


class VideoInfo(BaseModel):
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool


# --- Saída da IA -----------------------------------------------------------
# Sem restrições (ge/le) no schema: a validação de faixas é feita no
# pós-processamento, para não depender do suporte do structured outputs.

class ClipSuggestion(BaseModel):
    start: float
    end: float
    title: str
    hook: str
    reason: str
    score: int


class ClipSuggestions(BaseModel):
    clips: list[ClipSuggestion]


# --- Cortes do projeto -----------------------------------------------------

ClipStatus = Literal["pending", "rendering", "done", "error"]


class Clip(BaseModel):
    id: str
    project_id: str
    start: float
    end: float
    title: str
    hook: str = ""
    reason: str = ""
    score: int = 0
    style: str | None = None
    vertical_mode: str | None = None
    status: ClipStatus = "pending"
    output_path: str | None = None

    @property
    def duration(self) -> float:
        return self.end - self.start
