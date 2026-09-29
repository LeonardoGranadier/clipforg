from app import doctor
from app.config import Settings


def test_doctor_runs_all_checks():
    checks = doctor.run_all()
    assert checks, "doctor deveria retornar ao menos uma verificação"
    for c in checks:
        assert c.status in (doctor.OK, doctor.WARN, doctor.FAIL)
        assert c.detail


def test_api_key_is_never_fully_printed(monkeypatch, tmp_path):
    (tmp_path / ".env").write_text("")
    fake = "sk-ant-api03-SEGREDO-NAO-MOSTRAR-1234"
    monkeypatch.setattr(doctor, "settings", Settings(root=tmp_path, anthropic_api_key=fake))
    c = doctor.check_api_key()
    assert c.status == doctor.OK
    assert "SEGREDO" not in c.detail
