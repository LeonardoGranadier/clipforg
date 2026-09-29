import re
from pathlib import Path

import pytest

from app.errors import ClipForgeError
from app.models import Word
from app.pipeline import captions as cap
from app.pipeline.ffmpeg import filter_path


def W(text, start, end):
    return Word(word=text, start=start, end=end)


def seq(*texts, step=0.5, dur=0.4, t0=0.0):
    return [W(t, t0 + i * step, t0 + i * step + dur) for i, t in enumerate(texts)]


# --- agrupamento ------------------------------------------------------------

def test_groups_by_max_words():
    cues = cap.group_words(seq("a", "b", "c", "d", "e"), max_words=2)
    assert [c.text for c in cues] == ["a b", "c d", "e"]


def test_breaks_on_punctuation():
    cues = cap.group_words(seq("Olá,", "tudo", "bem?", "Sim"), max_words=3)
    assert [c.text for c in cues] == ["Olá,", "tudo bem?", "Sim"]


def test_breaks_on_long_pause():
    words = [W("um", 0, 0.3), W("dois", 1.0, 1.3), W("três", 1.35, 1.6)]
    cues = cap.group_words(words, max_words=3, pause=0.4)
    assert [c.text for c in cues] == ["um", "dois três"]


def test_respects_max_chars_and_two_lines():
    words = seq("extraordinariamente", "complicadíssimo", "inconstitucional", "sim", step=0.5)
    cues = cap.group_words(words, max_words=3, max_chars=20, max_lines=2)
    for c in cues:
        assert len(cap.wrap([w.word for w in c.words], 20)) <= 2


def test_cue_stays_until_next_on_short_gap_and_never_overlaps():
    cues = cap.group_words(seq("a", "b", "c", "d"), max_words=2)
    assert cues[0].end == cues[1].start  # sem piscar entre legendas
    for a, b in zip(cues, cues[1:]):
        assert a.end <= b.start


def test_words_in_range_is_relative_to_clip_start():
    words = seq("a", "b", "c", "d", t0=100.0)  # 100.0, 100.5, 101.0, 101.5
    rel = cap.words_in_range(words, 100.3, 101.45)
    assert [w.word for w in rel] == ["a", "b", "c"]
    assert rel[1].start == pytest.approx(0.2)  # 100.5 - 100.3
    assert rel[0].start == 0.0                 # palavra cortada no início é presa ao 0
    assert all(w.end <= 101.45 - 100.3 + 1e-9 for w in rel)
    assert cap.words_in_range(words, 100.4, 101.0)[0].word == "b"  # "a" termina exatamente em 100.4


# --- edições ---------------------------------------------------------------

def test_apply_edits_changes_text_keeps_times():
    words = seq("ola", "mundo", "eh")
    out = cap.apply_edits(words, {0: "Olá", 2: ""})
    assert [w.word for w in out] == ["Olá", "mundo"]
    assert out[0].start == words[0].start and out[0].end == words[0].end


def test_load_edits_rejects_garbage(tmp_path):
    p = tmp_path / "captions_edited.json"
    p.write_text('{"x": "y"}')
    with pytest.raises(ClipForgeError):
        cap.load_edits(p)


# --- .ass ------------------------------------------------------------------

def test_ass_time_format():
    assert cap.ass_time(0) == "0:00:00.00"
    assert cap.ass_time(61.234) == "0:01:01.23"
    assert cap.ass_time(3725.999) == "1:02:06.00"


def test_ass_color():
    assert cap.ass_color("#FFD400") == "&H0000D4FF"
    assert cap.ass_color("#000000", 0.6) == "&H66000000"
    with pytest.raises(ClipForgeError):
        cap.ass_color("amarelo")


def test_ass_escape():
    out = cap.ass_escape(r"{x} a\N b")
    assert "{" not in out.replace("\\{", "")
    assert "}" not in out.replace("\\}", "")
    assert "\\N" not in out


@pytest.mark.parametrize("style", cap.list_presets())
def test_all_presets_build_valid_ass(style):
    preset = cap.load_preset(style)
    words = seq("Ação", "é", "coração!", "Você", "vê")
    ass = cap.build_ass(cap.cues_for_preset(words, preset), preset)
    assert "PlayResX: 1080" in ass and "PlayResY: 1920" in ass
    assert "Ação" in ass or "AÇÃO" in ass
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert dialogues
    for d in dialogues:
        start, end = d.split(",")[1:3]
        assert re.fullmatch(r"\d:\d\d:\d\d\.\d\d", start)
        assert start < end


def test_margin_is_25_percent_from_bottom():
    preset = cap.load_preset("classic")
    ass = cap.build_ass([], preset)
    style_line = next(l for l in ass.splitlines() if l.startswith("Style:"))
    assert style_line.split(",")[-2] == str(round(0.25 * 1920))


def test_karaoke_highlights_each_word_once():
    preset = cap.load_preset("karaoke")
    ass = cap.build_ass(cap.cues_for_preset(seq("um", "dois", "três"), preset), preset)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 3
    for d, word in zip(dialogues, ["UM", "DOIS", "TRÊS"]):
        assert f"{{\\c&H0000D4FF}}{word}" in d


def test_pop_is_one_word_per_event():
    preset = cap.load_preset("pop")
    ass = cap.build_ass(cap.cues_for_preset(seq("um", "dois", "três"), preset), preset)
    dialogues = [l for l in ass.splitlines() if l.startswith("Dialogue:")]
    assert len(dialogues) == 3
    assert all("\\fscx" in d for d in dialogues)


def test_unknown_preset():
    with pytest.raises(ClipForgeError, match="não existe"):
        cap.load_preset("inexistente")


# --- .srt ------------------------------------------------------------------

def test_srt_format_and_long_lines():
    words = seq(*"Esta é uma frase bem longa para testar a quebra de linhas da legenda. Outra.".split())
    srt = cap.build_srt(words)
    blocks = srt.strip().split("\n\n")
    assert blocks[0].startswith("1\n00:00:00,000 --> ")
    for b in blocks:
        lines = b.split("\n")
        assert re.fullmatch(r"\d\d:\d\d:\d\d,\d{3} --> \d\d:\d\d:\d\d,\d{3}", lines[1])
        assert len(lines[2:]) <= 2
        assert all(len(l) <= 42 for l in lines[2:])
    assert blocks[-1].endswith("Outra.")  # quebra no fim da frase


# --- caminho para o filtro do FFmpeg ----------------------------------------

def test_filter_path_escapes_special_chars(tmp_path):
    p = tmp_path / "a:b'c[d],e;f" / "x.ass"
    out = filter_path(p)
    for ch in ":'[],;":
        assert f"\\{ch}" in out


def test_write_caption_files_empty_range(tmp_path):
    preset = cap.load_preset("classic")
    with pytest.raises(ClipForgeError, match="Não há falas"):
        cap.write_caption_files([], preset, tmp_path / "a.ass", tmp_path / "a.srt")
