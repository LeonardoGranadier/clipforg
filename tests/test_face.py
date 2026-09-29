import statistics

import pytest

from app.models import VideoInfo, Word
from app.pipeline import face
from app.pipeline.face import Face
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
    samples = face.analyze(sample_video, 1, 4, info)
    assert samples and all(not s for s in samples)
    path = face.follow_path(samples, None, 1)
    assert path.people == 0
    xs = face.crop_positions(path, info, 3)
    cw, ch = face.crop_box(info)
    assert set(xs) == {round(0.5 * info.width - cw / 2)}
    cmds = tmp_path / "face.txt"
    cmds.write_text(face.sendcmd_script(xs))
    out = cut(sample_video, tmp_path / "face.mp4", 1, 4, log_name="test-face",
              vf=vertical_filter("face", info, face=(cmds, cw, ch, xs[0])))
    o = probe(out)
    assert (o.width, o.height) == (1080, 1920)


# --- quem está falando ---------------------------------------------------------

FPS = face.SAMPLE_FPS


def two_people(n, talker, act_talk=0.6, act_quiet=0.03):
    """n amostras com duas pessoas (id 0 à esquerda, id 1 à direita); `talker(i)` diz quem fala."""
    out = []
    for i in range(n):
        t = talker(i)
        out.append({0: Face(cx=0.25, w=0.1, activity=act_talk if t == 0 else act_quiet),
                    1: Face(cx=0.75, w=0.12, activity=act_talk if t == 1 else act_quiet)})
    return out


def test_starts_on_who_is_talking_not_the_biggest_face():
    samples = two_people(4 * FPS, talker=lambda i: 0)  # o 0 fala, mas o rosto 1 é maior
    targets, switches = face.choose_targets(samples, [True] * len(samples))
    assert all(t == 0.25 for t in targets) and switches == []


def test_switches_to_the_new_speaker_within_a_second():
    n = 8 * FPS
    samples = two_people(n, talker=lambda i: 0 if i < n // 2 else 1)
    targets, switches = face.choose_targets(samples, [True] * n)
    assert len(switches) == 1
    lag = (switches[0] - n // 2) / FPS
    assert 0 < lag <= 1.2
    assert targets[-1] == 0.75


def test_brief_reaction_does_not_switch():
    # o ouvinte mexe a boca por 0,25 s (risada curta): não deve trocar
    n = 6 * FPS
    samples = two_people(n, talker=lambda i: 1 if 3 * FPS <= i < 3 * FPS + FPS // 4 else 0)
    targets, switches = face.choose_targets(samples, [True] * n)
    assert switches == [] and all(t == 0.25 for t in targets)


def test_no_switch_during_silence():
    # sem fala na transcrição, movimento de boca não conta (mastigar, sorrir...)
    n = 6 * FPS
    samples = two_people(n, talker=lambda i: 0 if i < FPS * 2 else 1)
    speech = [i < FPS * 2 for i in range(n)]
    targets, switches = face.choose_targets(samples, speech)
    assert switches == []


def test_holds_speaker_through_detection_dropout():
    n = 4 * FPS
    samples = two_people(n, talker=lambda i: 0)
    for i in range(FPS, FPS + FPS // 2):   # o rosto de quem fala some por 0,5 s
        del samples[i][0]
    targets, switches = face.choose_targets(samples, [True] * n)
    assert switches == []
    assert 0.75 not in targets   # nunca foi para o ouvinte


def test_speech_mask_from_words():
    words = [Word(word="oi", start=10.0, end=10.5), Word(word="tudo", start=12.0, end=12.4)]
    mask = face.speech_mask(words, start=10.0, n=3 * FPS)
    t = [i / FPS for i in range(3 * FPS)]
    assert all(m for m, x in zip(mask, t) if x <= 0.5)
    assert not any(m for m, x in zip(mask, t) if 0.8 <= x <= 1.8)
    assert face.speech_mask(None, 0, 5) == [True] * 5


def test_crop_cuts_instead_of_panning_on_speaker_switch():
    path = face.FacePath(centers=[0.25] * FPS + [0.75] * FPS, detected=2 * FPS)
    xs = face.crop_positions(path, INFO, 2.0)
    assert len(set(xs)) == 2  # só duas posições: corte seco, sem quadros intermediários
