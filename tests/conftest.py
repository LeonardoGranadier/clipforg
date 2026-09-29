import os
import tempfile

# Antes de importar o app: dados dos testes numa pasta temporária, nunca em data/.
os.environ["CLIPFORGE_DATA"] = tempfile.mkdtemp(prefix="clipforge-test-")

import shutil
import subprocess
from pathlib import Path

import pytest

from app.models import Segment, Transcript, Word


def make_transcript() -> Transcript:
    """Transcrição falsa: palavras de 0,4 s com 0,1 s de pausa, frases de 5 palavras."""
    segments, t, wid = [], 1.0, 0
    for sid in range(40):
        words = []
        for _ in range(5):
            words.append(Word(word=f"p{wid}", start=round(t, 3), end=round(t + 0.4, 3)))
            t += 0.5
            wid += 1
        t += 0.5  # pausa entre frases
        segments.append(Segment(id=sid, start=words[0].start, end=words[-1].end,
                                text=" ".join(w.word for w in words), words=words))
    return Transcript(language="pt", duration=round(t + 1, 3), segments=segments)


@pytest.fixture
def transcript() -> Transcript:
    return make_transcript()


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    """Vídeo de teste de 20 s gerado pelo próprio FFmpeg (testsrc + sine)."""
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg não instalado")
    out = tmp_path_factory.mktemp("media") / "amostra.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=640x360:rate=30:duration=20",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=20",
        "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out),
    ], check=True)
    return out


@pytest.fixture(scope="session")
def silent_video(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg"):
        pytest.skip("FFmpeg não instalado")
    out = tmp_path_factory.mktemp("media") / "mudo.mp4"
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=size=320x240:rate=25:duration=3",
        "-c:v", "libx264", "-preset", "ultrafast", str(out),
    ], check=True)
    return out
