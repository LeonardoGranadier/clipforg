"""Integração com FFmpeg real, usando vídeos sintéticos (sem arquivos externos)."""
import pytest

from app.errors import ClipForgeError
from app.pipeline.cut import cut
from app.pipeline.probe import probe


def test_probe_sample(sample_video):
    info = probe(sample_video)
    assert info.width == 640 and info.height == 360
    assert info.has_audio
    assert abs(info.duration - 20) < 0.2
    assert abs(info.fps - 30) < 0.1


def test_probe_detects_missing_audio(silent_video):
    assert probe(silent_video).has_audio is False


def test_probe_corrupt_file(tmp_path):
    bad = tmp_path / "quebrado.mp4"
    bad.write_bytes(b"isto nao e um video" * 100)
    with pytest.raises(ClipForgeError):
        probe(bad)


def test_probe_missing_file(tmp_path):
    with pytest.raises(ClipForgeError, match="não encontrado"):
        probe(tmp_path / "nao_existe.mp4")


def test_cut_is_frame_accurate(sample_video, tmp_path):
    progress = []
    out = cut(sample_video, tmp_path / "corte.mp4", 2.5, 7.5, log_name="test-cut",
              on_progress=progress.append)
    info = probe(out)
    assert abs(info.duration - 5.0) < 0.1
    assert info.has_audio
    assert progress[-1] == 1.0
    assert not list(tmp_path.glob("*.part*")), "arquivo temporário deveria ter sido removido"


def test_cut_video_without_audio(silent_video, tmp_path):
    out = cut(silent_video, tmp_path / "mudo_corte.mp4", 0.5, 2.0, log_name="test-cut-mudo")
    assert probe(out).has_audio is False


def test_cut_rejects_inverted_range(sample_video, tmp_path):
    with pytest.raises(ClipForgeError):
        cut(sample_video, tmp_path / "x.mp4", 5, 2, log_name="test-cut-inv")


def test_cut_with_burned_captions(sample_video, tmp_path):
    from app.models import Word
    from app.pipeline import captions as cap

    # pasta com caracteres que costumam quebrar o filtro do FFmpeg
    d = tmp_path / "pasta: d'água [x]"
    d.mkdir()
    words = [Word(word=w, start=3 + i * 0.5, end=3.4 + i * 0.5) for i, w in enumerate("Olá, ação e coração".split())]
    rel = cap.words_in_range(words, 2.5, 7.5)
    preset = cap.load_preset("karaoke")
    ass = d / "c.ass"
    cap.write_caption_files(rel, preset, ass, d / "c.srt", width=640, height=360)
    out = cut(sample_video, d / "c.mp4", 2.5, 7.5, log_name="test-cut-legenda", ass_path=ass)
    assert abs(probe(out).duration - 5.0) < 0.1
    assert (d / "c.srt").read_text(encoding="utf-8").startswith("1\n00:00:00,500 --> ")
