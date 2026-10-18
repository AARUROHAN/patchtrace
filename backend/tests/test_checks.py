from pathlib import Path
from app.services.check_runner import run_check


def test_python_compile_records_real_result(tmp_path):
    (tmp_path / "ok.py").write_text("def hello():\n    return 'hello'\n")
    result = run_check(tmp_path, "python_compile", False)
    assert result["status"] == "passed"
    assert result["return_code"] == 0
    assert "compileall" in result["command"]


def test_pytest_is_not_run_without_explicit_confirmation(tmp_path):
    (tmp_path / "test_danger.py").write_text("def test_ok():\n    assert True\n")
    result = run_check(tmp_path, "pytest", False)
    assert result["status"] == "not_run"
    assert result["return_code"] is None
    assert "Confirmation" in result["detail"]
