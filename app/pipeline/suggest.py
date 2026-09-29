"""Sugestão de cortes com a API do Claude."""
from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Any

from pydantic import ValidationError

from app.config import settings
from app.errors import Cancelled, ClipForgeError
from app.models import Clip, ClipSuggestion, ClipSuggestions, Segment, Transcript
from app.pipeline.snap import snap_range

log = logging.getLogger(__name__)

BLOCK_SECONDS = 12 * 60
OVERLAP_SECONDS = 30
MAX_ATTEMPTS = 3  # 1 tentativa + 2 novas tentativas

SYSTEM_PROMPT = """\
Você é um editor de vídeo especialista em cortes curtos para TikTok, Reels e Shorts.
Receberá a transcrição de um trecho de vídeo, uma linha por frase, no formato
[início → fim] texto (tempos em mm:ss.s ou h:mm:ss.s).

Escolha os melhores trechos para virar cortes independentes. Regras:
- Duração de cada corte entre {min_s} e {max_s} segundos; o ideal é de {target_min} a {target_max} s.
- Gancho forte nos primeiros 3 segundos: pergunta, afirmação polêmica, número, promessa ou início de história.
- A ideia precisa estar completa, com começo, meio e fim naturais. Nunca termine no meio de um raciocínio.
- Comece no início de uma frase e termine no fim de uma frase (use os tempos das linhas).
- Não repita o mesmo assunto em dois cortes.
- Score honesto de 0 a 100 (o potencial de viralizar/reter), com justificativa curta em "reason".
- Sugira de 3 a 8 cortes; menos se o trecho não tiver bons momentos. Qualidade vale mais que quantidade.
- "title", "hook" e "reason" no mesmo idioma do vídeo ({language}). "hook" é a frase de abertura do corte.
- "start" e "end" em segundos (número decimal), no tempo absoluto do vídeo.
"""


def fmt_time(t: float) -> str:
    h, rem = divmod(t, 3600)
    m, s = divmod(rem, 60)
    return f"{int(h)}:{int(m):02d}:{s:04.1f}" if h else f"{int(m):02d}:{s:04.1f}"


def format_segments(segments: list[Segment]) -> str:
    return "\n".join(f"[{fmt_time(s.start)} → {fmt_time(s.end)}] {s.text}" for s in segments)


def split_blocks(transcript: Transcript, block: float = BLOCK_SECONDS,
                 overlap: float = OVERLAP_SECONDS) -> list[list[Segment]]:
    """Divide em blocos de ~12 min com 30 s de sobreposição (vídeos longos)."""
    segs = transcript.segments
    if not segs:
        return []
    total = segs[-1].end
    if total <= block + overlap:
        return [segs]
    blocks, t0 = [], 0.0
    while t0 < total:
        t1 = t0 + block
        chunk = [s for s in segs if s.end > t0 and s.start < t1]
        if chunk:
            blocks.append(chunk)
        t0 = t1 - overlap
    return blocks


def _request_block(client: Any, segments: list[Segment], language: str) -> list[ClipSuggestion]:
    """Pede sugestões para um bloco, validando o JSON e tentando de novo se vier inválido."""
    system = SYSTEM_PROMPT.format(
        min_s=settings.clip_min_seconds, max_s=settings.clip_max_seconds,
        target_min=30, target_max=60, language=language,
    )
    user = "Transcrição:\n\n" + format_segments(segments)
    last_error = ""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = client.messages.parse(
                model=settings.claude_model,
                max_tokens=16000,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=ClipSuggestions,
            )
        except ValidationError as e:
            last_error = f"JSON inválido: {e.error_count()} erro(s)"
        else:
            if resp.stop_reason == "refusal":
                raise ClipForgeError("O Claude recusou analisar este trecho.")
            if resp.stop_reason == "max_tokens":
                last_error = "resposta cortada (max_tokens)"
            elif resp.parsed_output is None:
                last_error = "resposta sem JSON válido"
            else:
                return resp.parsed_output.clips
        log.warning("Sugestão: tentativa %d/%d falhou: %s", attempt, MAX_ATTEMPTS, last_error)
    raise ClipForgeError(f"O Claude não retornou um JSON válido após {MAX_ATTEMPTS} tentativas ({last_error}).")


def _overlap_ratio(a: Clip, b: Clip) -> float:
    inter = min(a.end, b.end) - max(a.start, b.start)
    return max(0.0, inter) / max(1e-6, min(a.duration, b.duration))


def postprocess(raw: list[ClipSuggestion], transcript: Transcript, project_id: str) -> list[Clip]:
    """Ajusta às palavras, adiciona folga, filtra pela duração, remove repetidos e ordena por score."""
    words = transcript.all_words()
    duration = transcript.duration
    clips: list[Clip] = []
    for s in raw:
        if s.end <= s.start:
            continue
        start, end = snap_range(s.start, s.end, words, duration)
        clip = Clip(
            id="", project_id=project_id, start=start, end=end,
            title=s.title.strip(), hook=s.hook.strip(), reason=s.reason.strip(),
            score=max(0, min(100, s.score)),
        )
        if not settings.clip_min_seconds <= clip.duration <= settings.clip_max_seconds:
            log.info("Descartado (%.1fs fora da faixa): %s", clip.duration, clip.title)
            continue
        clips.append(clip)

    clips.sort(key=lambda c: c.score, reverse=True)
    kept: list[Clip] = []
    for c in clips:  # os blocos se sobrepõem: fica o de maior score
        if all(_overlap_ratio(c, k) <= 0.5 for k in kept):
            kept.append(c)
    for n, c in enumerate(kept, 1):
        c.id = f"c{n:02d}"
    return kept


def make_client() -> Any:
    if not settings.anthropic_api_key:
        raise ClipForgeError(
            "ANTHROPIC_API_KEY não está definida no .env. Sem ela não há sugestões por IA, "
            "mas você ainda pode criar cortes manualmente."
        )
    import anthropic
    return anthropic.Anthropic(api_key=settings.anthropic_api_key)


def suggest_clips(
    transcript: Transcript,
    project_id: str,
    *,
    client: Any | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: threading.Event | None = None,
) -> list[Clip]:
    import anthropic

    client = client or make_client()
    blocks = split_blocks(transcript)
    raw: list[ClipSuggestion] = []
    for i, block in enumerate(blocks):
        if cancel is not None and cancel.is_set():
            raise Cancelled()
        try:
            raw.extend(_request_block(client, block, transcript.language))
        except anthropic.AuthenticationError as e:
            raise ClipForgeError("Chave da API inválida. Confira ANTHROPIC_API_KEY no .env.") from e
        except anthropic.NotFoundError as e:
            raise ClipForgeError(f"Modelo '{settings.claude_model}' não encontrado. Confira CLAUDE_MODEL no .env.") from e
        except anthropic.RateLimitError as e:
            raise ClipForgeError("Limite de uso da API atingido. Espere um pouco e tente de novo.") from e
        except anthropic.APIConnectionError as e:
            raise ClipForgeError("Sem conexão com a API do Claude. Confira sua internet.") from e
        except anthropic.APIStatusError as e:
            raise ClipForgeError(f"Erro da API do Claude ({e.status_code}). Tente de novo mais tarde.") from e
        if on_progress:
            on_progress((i + 1) / len(blocks))
    return postprocess(raw, transcript, project_id)
