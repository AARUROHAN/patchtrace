from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any

TIMEOUT = int(os.getenv("PATCHTRACE_CHECK_TIMEOUT_SECONDS", "25"))


def run_check(root: Path, check_id: str, confirm_execution: bool) -> dict[str, Any]:
    public_mode = os.getenv("PATCHTRACE_PUBLIC_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
    if public_mode and check_id == "pytest":
        return {"id": check_id, "status": "not_run", "command": "python -m pytest -q", "detail": "Disabled on the public deployment because repository tests can execute arbitrary code.", "return_code": None, "output": ""}
    if check_id == "python_compile":
        command = [sys.executable, "-m", "compileall", "-q", "."]
        risk = "This compiles Python files; it does not import or run the application code."
    elif check_id == "pytest":
        if not confirm_execution:
            return {"id": check_id, "status": "not_run", "command": "python -m pytest -q", "detail": "Confirmation is required because tests execute repository code.", "return_code": None, "output": ""}
        command = [sys.executable, "-m", "pytest", "-q"]
        risk = "User confirmed test execution. Tests can execute arbitrary code; do not run against untrusted repositories outside a disposable environment."
    else:
        return {"id": check_id, "status": "not_run", "command": "(unsupported)", "detail": "This check is not supported by the prototype.", "return_code": None, "output": ""}
    try:
        proc = subprocess.run(command, cwd=root, capture_output=True, text=True, timeout=TIMEOUT, check=False, env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "PIP_DISABLE_PIP_VERSION_CHECK": "1"})
        output = ((proc.stdout or "") + (proc.stderr or ""))[-5000:]
        return {"id": check_id, "status": "passed" if proc.returncode == 0 else "failed", "command": "python -m " + ("compileall -q ." if check_id == "python_compile" else "pytest -q"), "detail": risk, "return_code": proc.returncode, "output": output or "(No output)", "duration_note": f"Completed within the {TIMEOUT}s limit."}
    except subprocess.TimeoutExpired as exc:
        output = ((exc.stdout or "") if isinstance(exc.stdout, str) else "") + ((exc.stderr or "") if isinstance(exc.stderr, str) else "")
        return {"id": check_id, "status": "failed", "command": "python -m " + ("compileall -q ." if check_id == "python_compile" else "pytest -q"), "detail": f"Timed out after {TIMEOUT}s; partial output follows.", "return_code": None, "output": output[-5000:]}
    except OSError as exc:
        return {"id": check_id, "status": "failed", "command": "python -m " + ("compileall -q ." if check_id == "python_compile" else "pytest -q"), "detail": f"Could not start check: {exc}", "return_code": None, "output": ""}
