from app.models import Word
from app.pipeline.transcribe import MIN_WORD, fix_word_times


def test_fix_word_times_repairs_bad_timestamps():
    words = [
        Word(word="a", start=1.0, end=1.4),
        Word(word="b", start=1.5, end=1.5),   # end <= start
        Word(word="c", start=1.3, end=2.0),   # começa antes do fim da anterior
        Word(word="d", start=2.2, end=2.6),
    ]
    fixed = fix_word_times(words, 1.0, 3.0)
    for w in fixed:
        assert w.end > w.start
    for a, b in zip(fixed, fixed[1:]):
        assert b.start >= a.end
    assert fixed[1].end - fixed[1].start >= MIN_WORD


def test_fix_word_times_keeps_good_timestamps():
    words = [Word(word="a", start=0.0, end=0.5), Word(word="b", start=0.6, end=1.0)]
    assert fix_word_times(words, 0.0, 1.0) == words
