from app.services.check_runner import run_check


def test_public_mode_blocks_pytest_execution(tmp_path, monkeypatch):
    monkeypatch.setenv("PATCHTRACE_PUBLIC_MODE", "true")
    result = run_check(tmp_path, "pytest", confirm_execution=True)
    assert result["status"] == "not_run"
    assert "Disabled on the public deployment" in result["detail"]
