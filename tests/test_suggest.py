from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.config import Settings
from app.errors import ClipForgeError
from app.models import ClipSuggestion, ClipSuggestions, Segment, Transcript, Word
from app.pipeline import suggest


def sug(start, end, score=50, title="t"):
    return ClipSuggestion(start=start, end=end, title=title, hook="h", reason="r", score=score)


class FakeClient:
    """Imita client.messages.parse devolvendo respostas pré-definidas, sem gastar API."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0
        self.messages = self

    def parse(self, **kwargs):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def ok(clips):
    return SimpleNamespace(stop_reason="end_turn", parsed_output=ClipSuggestions(clips=clips))


# --- validação do JSON ------------------------------------------------------

def test_valid_llm_json_parses():
    raw = '{"clips":[{"start":125.4,"end":171.2,"title":"Tít","hook":"Gancho","reason":"Bom","score":87}]}'
    parsed = ClipSuggestions.model_validate_json(raw)
    assert parsed.clips[0].score == 87


@pytest.mark.parametrize("raw", [
    '{"clips":[{"start":1,"end":2}]}',               # campos faltando
    '{"clips":[{"start":"x","end":2,"title":"a","hook":"b","reason":"c","score":1}]}',
    'não é json',
])
def test_invalid_llm_json_rejected(raw):
    with pytest.raises(ValidationError):
        ClipSuggestions.model_validate_json(raw)


# --- novas tentativas -------------------------------------------------------

def test_retries_until_valid(transcript):
    bad = SimpleNamespace(stop_reason="end_turn", parsed_output=None)
    client = FakeClient([bad, ok([sug(5.0, 30.0, 80)])])
    clips = suggest.suggest_clips(transcript, "proj", client=client)
    assert client.calls == 2
    assert len(clips) == 1


def test_gives_up_after_max_attempts(transcript):
    bad = SimpleNamespace(stop_reason="end_turn", parsed_output=None)
    client = FakeClient([bad] * suggest.MAX_ATTEMPTS)
    with pytest.raises(ClipForgeError):
        suggest.suggest_clips(transcript, "proj", client=client)
    assert client.calls == suggest.MAX_ATTEMPTS


def test_missing_api_key_is_clear(monkeypatch, transcript):
    monkeypatch.setattr(suggest, "settings", Settings(anthropic_api_key=""))
    with pytest.raises(ClipForgeError, match="ANTHROPIC_API_KEY"):
        suggest.suggest_clips(transcript, "proj")


# --- pós-processamento ------------------------------------------------------

def test_postprocess_filters_sorts_and_dedupes(transcript):
    raw = [
        sug(5.0, 10.0, 99, "curto demais"),
        sug(5.0, 40.0, 60, "a"),
        sug(6.0, 41.0, 70, "a repetido com score maior"),
        sug(50.0, 90.0, 90, "b"),
        sug(30.0, 20.0, 95, "fim antes do início"),
    ]
    clips = suggest.postprocess(raw, transcript, "proj")
    assert [c.title for c in clips] == ["b", "a repetido com score maior"]
    assert [c.id for c in clips] == ["c01", "c02"]
    words = transcript.all_words()
    for c in clips:
        assert not any(w.start < c.start < w.end or w.start < c.end < w.end for w in words)


def test_score_is_clamped(transcript):
    clips = suggest.postprocess([sug(5.0, 40.0, 150)], transcript, "proj")
    assert clips[0].score == 100


# --- formatação e blocos ----------------------------------------------------

def test_fmt_time():
    assert suggest.fmt_time(65.25) == "01:05.2"
    assert suggest.fmt_time(3725.0) == "1:02:05.0"


def test_split_blocks_short_video_is_one_block(transcript):
    assert len(suggest.split_blocks(transcript)) == 1


def test_split_blocks_long_video_overlaps():
    segs = []
    for i in range(0, 1800, 10):  # 30 min, uma frase a cada 10 s
        w = Word(word="x", start=i, end=i + 5)
        segs.append(Segment(id=len(segs), start=i, end=i + 5, text="x", words=[w]))
    t = Transcript(language="pt", duration=1800, segments=segs)
    blocks = suggest.split_blocks(t)
    assert len(blocks) == 3
    # sobreposição: o fim de um bloco aparece no início do seguinte
    assert blocks[0][-1].start >= blocks[1][0].start
    covered = {s.id for b in blocks for s in b}
    assert covered == {s.id for s in segs}
