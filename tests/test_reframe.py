import shutil
import subprocess

import pytest

from app.models import VideoInfo, Word
from app.pipeline import captions as cap
from app.pipeline.cut import cut
from app.pipeline.probe import probe
from app.pipeline.reframe import MODES, is_vertical, vertical_filter

H169 = VideoInfo(duration=10, width=1920, height=1080, fps=30, has_audio=True)
V916 = VideoInfo(duration=10, width=576, height=1024, fps=30, has_audio=True)


def test_is_vertical():
    assert not is_vertical(H169)
    assert is_vertical(V916)


@pytest.mark.parametrize("mode", MODES)
def test_filters_end_in_1080x1920_30fps(mode):
    vf = vertical_filter(mode, H169)
    assert vf.endswith(",fps=30,setsar=1")
    assert "1080" in vf and "1920" in vf


def test_vertical_source_is_not_reframed():
    assert vertical_filter("center", V916) == vertical_filter("fit", V916)
    assert vertical_filter("blur", V916) == vertical_filter("fit", V916)


def test_unknown_mode():
    with pytest.raises(ValueError):
        vertical_filter("zoom", H169)  # type: ignore[arg-type]


def test_pop_shrinks_long_words():
    assert "\\fscx100" in cap.pop_tags(5)
    assert "\\fscx73" in cap.pop_tags(22)


# --- integração -------------------------------------------------------------

@pytest.fixture(scope="module")
def portrait_video(tmp_path_factory):
    """Vídeo vertical 3:4 (mais largo que 9:16), para testar o fit com barras."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg não instalado")
    out = tmp_path_factory.mktemp("media") / "retrato.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=480x640:rate=25:duration=4",
        "-f", "lavfi", "-i", "sine=duration=4",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out


@pytest.mark.parametrize("mode", MODES)
def test_render_vertical_horizontal_source(sample_video, tmp_path, mode):
    info = probe(sample_video)
    words = [Word(word=w, start=1 + i * 0.4, end=1.35 + i * 0.4) for i, w in enumerate("Olá ação coração".split())]
    ass = tmp_path / "c.ass"
    cap.write_caption_files(cap.words_in_range(words, 0.5, 3.5), cap.load_preset("classic"),
                            ass, tmp_path / "c.srt")
    out = cut(sample_video, tmp_path / f"{mode}.mp4", 0.5, 3.5, log_name=f"test-vertical-{mode}",
              vf=vertical_filter(mode, info), ass_path=ass)
    o = probe(out)
    assert (o.width, o.height) == (1080, 1920)
    assert abs(o.fps - 30) < 0.1
    assert abs(o.duration - 3.0) < 0.1


def test_render_vertical_portrait_source(portrait_video, tmp_path):
    info = probe(portrait_video)
    assert is_vertical(info)
    out = cut(portrait_video, tmp_path / "p.mp4", 0, 2, log_name="test-vertical-retrato",
              vf=vertical_filter("center", info))
    o = probe(out)
    assert (o.width, o.height) == (1080, 1920)
