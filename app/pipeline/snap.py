"""Ajusta início/fim de um corte às palavras, para nunca cortar uma palavra no meio."""
from __future__ import annotations

from bisect import bisect_left

from app.models import Word

PAD_BEFORE = 0.15
PAD_AFTER = 0.25


def _nearest(values: list[float], t: float, lo: int = 0) -> int:
    i = bisect_left(values, t, lo=lo)
    if i >= len(values):
        return len(values) - 1
    if i > lo and t - values[i - 1] <= values[i] - t:
        return i - 1
    return i


def snap_range(
    start: float,
    end: float,
    words: list[Word],
    duration: float,
    pad_before: float = PAD_BEFORE,
    pad_after: float = PAD_AFTER,
) -> tuple[float, float]:
    """Retorna (start, end) ajustados.

    - O início vai para o começo da palavra mais próxima; o fim, para o final da palavra mais próxima.
    - A folga nunca invade a palavra vizinha nem passa dos limites do vídeo.
    """
    if not words:
        return max(0.0, start), min(duration, end)
    starts = [w.start for w in words]
    ends = [w.end for w in words]

    i = _nearest(starts, start)
    j = _nearest(ends, end, lo=i)

    prev_end = words[i - 1].end if i > 0 else 0.0
    next_start = words[j + 1].start if j + 1 < len(words) else duration
    new_start = max(0.0, prev_end, words[i].start - pad_before)
    new_end = min(duration, next_start, words[j].end + pad_after)
    return round(new_start, 3), round(new_end, 3)
