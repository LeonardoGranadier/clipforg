import statistics

import pytest

from app.models import VideoInfo
from app.pipeline import face
from app.pipeline.cut import cut
from app.pipeline.probe import probe
from app.pipeline.reframe import vertical_filter

INFO = VideoInfo(duration=20, width=1920, height=1080, fps=30, has_audio=True)


def test_crop_box_is_9_16_and_even():
    cw, ch = face.crop_box(INFO)
    assert (cw, ch) == (606, 1080) or (cw, ch) == (608, 1080)
    assert cw % 2 == 0 and ch % 2 == 0


def test_noisy_still_face_barely_moves():
    # rosto parado em 0.6 com ruído de ±2% (tremor do detector)
    raw = [0.6 + (0.02 if k % 2 else -0.02) for k in range(80)]
    path = face.smooth_path(raw)
    assert statistics.pstdev(path.centers) < 0.005
    assert all(abs(c - 0.6) < face.DEAD_ZONE for c in path.centers)


def test_follows_moving_face_with_speed_limit():
    raw = [0.2 + 0.5 * k / 79 for k in range(80)]  # anda de 0.2 a 0.7 em 20 s
    path = face.smooth_path(raw)
    steps = [abs(b - a) for a, b in zip(path.centers, path.centers[1:])]
    assert max(steps) <= face.MAX_SPEED / face.SAMPLE_FPS + 1e-9
    assert abs(path.centers[-1] - 0.7) < face.DEAD_ZONE + 0.01


def test_camera_cut_jumps_instead_of_panning():
    raw = [0.2] * 20 + [0.8] * 20
    path = face.smooth_path(raw)
    assert path.centers[20 + face.JUMP_SAMPLES] == pytest.approx(0.8)


def test_single_outlier_is_ignored():
    raw = [0.3] * 10 + [0.9] + [0.3] * 10  # um falso positivo isolado
    path = face.smooth_path(raw)
    assert max(path.centers) < 0.35


def test_holds_one_second_then_returns_to_center():
    raw = [0.2] * 8 + [None] * 40
    path = face.smooth_path(raw)
    hold = round(face.HOLD_SECONDS * face.SAMPLE_FPS)
    assert path.centers[8 + hold - 1] == pytest.approx(0.2)   # ainda parado no último lugar
    assert abs(path.centers[-1] - 0.5) < 0.05                  # voltou ao centro
    steps = [abs(b - a) for a, b in zip(path.centers, path.centers[1:])]
    assert max(steps) <= face.MAX_SPEED / face.SAMPLE_FPS + 1e-9  # devagar, sem pulo


def test_no_face_at_all_stays_centered():
    path = face.smooth_path([None] * 20)
    assert all(c == 0.5 for c in path.centers)
    assert path.detected == 0


def test_crop_positions_stay_inside_frame():
    path = face.FacePath(centers=[0.0, 0.0, 1.0, 1.0], detected=4)
    xs = face.crop_positions(path, INFO, 1.0)
    cw, _ = face.crop_box(INFO)
    assert min(xs) == 0 and max(xs) == INFO.width - cw
    assert len(xs) == 30


def test_sendcmd_script_only_on_change():
    script = face.sendcmd_script([10, 10, 12, 12, 12, 15])
    assert script.splitlines() == ["0.0000 crop@face x 10;", "0.0667 crop@face x 12;", "0.1667 crop@face x 15;"]


def test_face_render_without_faces_is_centered(sample_video, tmp_path):
    """Integração: vídeo sem rosto -> recorte central, saída 1080x1920."""
    info = probe(sample_video)
    raw = face.sample_faces(sample_video, 1, 4, info)
    assert raw and all(r is None for r in raw)
    xs = face.crop_positions(face.smooth_path(raw), info, 3)
    cw, ch = face.crop_box(info)
    assert set(xs) == {round(0.5 * info.width - cw / 2)}
    cmds = tmp_path / "face.txt"
    cmds.write_text(face.sendcmd_script(xs))
    out = cut(sample_video, tmp_path / "face.mp4", 1, 4, log_name="test-face",
              vf=vertical_filter("face", info, face=(cmds, cw, ch, xs[0])))
    o = probe(out)
    assert (o.width, o.height) == (1080, 1920)
