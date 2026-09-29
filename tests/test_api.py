"""API web de ponta a ponta, com vídeo sintético e transcrição falsa (sem Whisper nem Claude)."""
import pytest
from fastapi.testclient import TestClient

from app.jobs import jobs
from app.main import app
from app.models import Segment, Transcript, Word
from app.projects import get_project


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


def fake_transcript() -> Transcript:
    frases = ["Você sabia que isso muda tudo?", "A resposta é simples.", "Faça todo dia."]
    segs, t = [], 1.0
    for i, f in enumerate(frases):
        ws = []
        for w in f.split():
            ws.append(Word(word=w, start=round(t, 2), end=round(t + 0.35, 2)))
            t += 0.45
        segs.append(Segment(id=i, start=ws[0].start, end=ws[-1].end, text=f, words=ws))
        t += 0.8
    return Transcript(language="pt", duration=20.0, segments=segs)


@pytest.fixture(scope="module")
def project_id(client, sample_video):
    with open(sample_video, "rb") as f:
        r = client.post("/api/projects", files={"file": ("Meu Vídeo.mp4", f, "video/mp4")})
    assert r.status_code == 201, r.text
    pid = r.json()["id"]
    get_project(pid).transcript_path.write_text(fake_transcript().model_dump_json(), encoding="utf-8")
    return pid


def test_upload_lists_project(client, project_id):
    projects = client.get("/api/projects").json()
    p = next(p for p in projects if p["id"] == project_id)
    assert p["name"] == "Meu Vídeo.mp4"
    assert p["info"]["width"] == 640
    assert p["has_transcript"]


def test_upload_rejects_non_video(client):
    r = client.post("/api/projects", files={"file": ("nota.txt", b"oi", "text/plain")})
    assert r.status_code == 400
    assert "não suportado" in r.json()["detail"]


def test_upload_rejects_video_without_audio(client, silent_video):
    with open(silent_video, "rb") as f:
        r = client.post("/api/projects", files={"file": ("mudo.mp4", f, "video/mp4")})
    assert r.status_code == 400
    assert "áudio" in r.json()["detail"]


def test_unknown_project_is_404(client):
    assert client.get("/api/projects/nao-existe").status_code == 404


def test_transcript_and_edits(client, project_id):
    t = client.get(f"/api/projects/{project_id}/transcript").json()
    assert t["words"][0]["word"] == "Você"
    r = client.put(f"/api/projects/{project_id}/captions", json={"edits": {"0": "VOCÊ", "1": ""}})
    assert r.status_code == 200
    t = client.get(f"/api/projects/{project_id}/transcript").json()
    assert t["edits"] == {"0": "VOCÊ", "1": ""}
    cues = client.get(f"/api/projects/{project_id}/captions/cues?style=classic").json()["cues"]
    words = [w["word"] for c in cues for w in c["words"]]
    assert words[0] == "VOCÊ" and "sabia" not in words
    assert client.put(f"/api/projects/{project_id}/captions", json={"edits": {"999": "x"}}).status_code == 400


def test_snap(client, project_id):
    r = client.post(f"/api/projects/{project_id}/snap", json={"start": 1.2, "end": 3.1}).json()
    assert r["start"] <= 1.0 and r["end"] >= 3.1


def test_clips_crud_and_render(client, project_id):
    body = [{"start": 1.0, "end": 4.0, "title": "Primeiro"}, {"start": 5.0, "end": 8.0, "title": "Segundo"}]
    clips = client.put(f"/api/projects/{project_id}/clips", json=body).json()
    assert [c["id"] for c in clips] == ["m01", "m02"]

    job = client.post(f"/api/projects/{project_id}/clips/m01/render",
                      json={"style": "karaoke", "vertical": "blur", "resolution": "720p"}).json()
    jobs.wait_idle(120)
    job = client.get(f"/api/jobs/{job['id']}").json()
    assert job["status"] == "done", job
    c = client.get(f"/api/projects/{project_id}/clips").json()[0]
    assert c["status"] == "done" and c["style"] == "karaoke" and c["vertical_mode"] == "blur"
    assert c["files"]["mp4"] and c["files"]["srt"] and c["files"]["ass"]

    # o player precisa de Range
    r = client.get(c["files"]["mp4"], headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100
    r = client.get(c["files"]["mp4"] + "?download=1")
    assert "attachment" in r.headers["content-disposition"]

    # mudar só o título mantém o render; mudar o fim invalida
    body = [{"id": "m01", "start": c["start"], "end": c["end"], "title": "Novo título"},
            {"id": "m02", "start": 5.0, "end": 8.0, "title": "Segundo"}]
    assert client.put(f"/api/projects/{project_id}/clips", json=body).json()[0]["status"] == "done"
    body[0]["end"] = 3.5
    assert client.put(f"/api/projects/{project_id}/clips", json=body).json()[0]["status"] == "pending"


def test_invalid_clip_and_render_options(client, project_id):
    r = client.put(f"/api/projects/{project_id}/clips", json=[{"start": 5, "end": 2}])
    assert r.status_code == 400
    r = client.post(f"/api/projects/{project_id}/clips/m01/render", json={"vertical": "zoom"})
    assert r.status_code == 400
    r = client.post(f"/api/projects/{project_id}/clips/zz99/render", json={})
    assert r.status_code == 404


def test_cancel_queued_job(client, project_id):
    client.put(f"/api/projects/{project_id}/clips", json=[{"id": "m01", "start": 1, "end": 8, "title": "a"},
                                                          {"id": "m02", "start": 1, "end": 8, "title": "b"}])
    jobs_ = client.post(f"/api/projects/{project_id}/render-all", json={"vertical": "fit"}).json()
    r = client.post(f"/api/jobs/{jobs_[1]['id']}/cancel").json()
    assert r["status"] in ("cancelled", "running")
    jobs.wait_idle(120)
    assert client.get(f"/api/jobs/{jobs_[1]['id']}").json()["status"] == "cancelled"


def test_export_captions_only(client, project_id):
    r = client.post(f"/api/projects/{project_id}/captions/export", json={"style": "pop"}).json()
    assert r["srt"].endswith(".srt") and r["ass"].endswith(".pop.ass")
    assert client.get(r["srt"]).text.startswith("1\n")


def test_media_blocks_path_traversal(client, project_id):
    assert client.get(f"/media/{project_id}/../../clipforge.db").status_code == 404
    assert client.get(f"/media/{project_id}/transcript.json").status_code == 404
