from app.models import Word
from app.pipeline.snap import snap_range


def inside_a_word(t: float, words: list[Word]) -> bool:
    return any(w.start < t < w.end for w in words)


def test_snaps_to_nearest_word_boundaries(transcript):
    words = transcript.all_words()
    # palavra p2 = 2.0–2.4, p9 = 5.5–5.9 (ver conftest); tempos pedidos caem no meio delas
    start, end = snap_range(2.1, 5.7, words, transcript.duration)
    assert not inside_a_word(start, words)
    assert not inside_a_word(end, words)
    assert start <= 2.0 and end >= 5.9


def test_padding_never_enters_neighbor_word():
    words = [Word(word="a", start=0.0, end=1.0), Word(word="b", start=1.05, end=2.0),
             Word(word="c", start=2.1, end=3.0)]
    start, end = snap_range(1.05, 2.0, words, 3.0)
    assert start == 1.0  # folga de 0,15 s limitada pelo fim de "a"
    assert end == 2.1    # folga de 0,25 s limitada pelo início de "c"


def test_padding_respects_video_bounds():
    words = [Word(word="a", start=0.05, end=0.5), Word(word="b", start=0.6, end=0.9)]
    start, end = snap_range(0.0, 0.9, words, 1.0)
    assert start == 0.0
    assert end == 1.0


def test_end_never_before_start(transcript):
    words = transcript.all_words()
    start, end = snap_range(10.0, 10.1, words, transcript.duration)
    assert end > start


def test_no_words_just_clamps():
    assert snap_range(-1, 50, [], 30) == (0.0, 30)
