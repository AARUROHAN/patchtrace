from fastapi.testclient import TestClient
from app.main import app

client = TestClient(app)


def test_health_is_local_first_and_model_optional():
    response = client.get("/api/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["product"] == "PatchTrace"
    assert "model" in body


def test_demo_endpoint_labels_synthetic_source():
    response = client.get("/api/demo")
    assert response.status_code == 200
    assert response.json()["synthetic"] is True
    assert "Synthetic" in response.json()["source_label"]


def test_inspection_returns_manifest_evidence_and_artifact():
    response = client.post("/api/inspect", json={
        "source_type": "demo",
        "source": "",
        "task": "The README quick-start command references a missing script; propose a documentation correction.",
        "use_ai": False,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["overview"]["file_count"] > 0
    assert body["evidence"]
    assert "Contribution Brief" in body["artifact"]
    assert body["proposed_diff"] is not None
    assert body["ai"]["used"] is False
    assert body["proposed_diff"]["status"] == "proposed_not_applied"
    # A real compile check runs against the bounded snapshot and returns its exit status.
    check = client.post(f"/api/checks/{body['repo_id']}", json={"check_id": "python_compile", "confirm_execution": False})
    assert check.status_code == 200, check.text
    assert check.json()["status"] == "passed"


def test_inspection_validates_task_length():
    response = client.post("/api/inspect", json={"source_type": "demo", "task": "tiny"})
    assert response.status_code == 422


def test_inspection_without_gemma_key_falls_back_without_crashing(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    response = client.post("/api/inspect", json={
        "source_type": "demo",
        "source": "",
        "task": "Explain the README command mismatch and propose a docs-only correction.",
        "use_ai": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["ai"]["used"] is False
    assert "GEMINI_API_KEY is missing" in body["ai"]["error"]
    assert body["proposed_diff"]["status"] == "proposed_not_applied"


def test_public_mode_rejects_local_filesystem_paths(monkeypatch, tmp_path):
    import app.main as api

    monkeypatch.setattr(api, "PUBLIC_MODE", True)
    response = client.post("/api/inspect", json={
        "source_type": "local",
        "source": str(tmp_path),
        "task": "Review the repository layout and locate the setup instructions.",
        "use_ai": False,
    })
    assert response.status_code == 400
    assert "Local filesystem paths are disabled" in response.json()["detail"]



def test_model_response_is_not_labelled_as_verified_pass(monkeypatch):
    import app.main as api

    model_result = {
        "summary": "The setup command may not match the available entry point; verify before editing.",
        "likely_files": [],
        "plan": ["Inspect the command", "Compare it with the entry point", "Verify in a safe environment"],
        "questions": [],
        "risks": ["The command has not been executed"],
    }
    monkeypatch.setattr(api, "analyze_task", lambda *args, **kwargs: (model_result, None))
    monkeypatch.setattr(api, "model_config", lambda: {
        "configured": True, "provider": "Gemma API (test)", "provider_id": "gemma_api", "model": "test-model"
    })

    response = client.post("/api/inspect", json={
        "source_type": "demo",
        "source": "",
        "task": "Review the README quick-start command and compare it with the actual entry point.",
        "use_ai": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    model_check = next(check for check in body["checks"] if check["id"] == "model")
    assert model_check["status"] == "responded"
    assert "not a verified bug" in model_check["detail"]


def test_partial_model_response_is_labelled_partial(monkeypatch):
    import app.main as api

    model_result = {
        "summary": "A possible setup mismatch is visible, but the exact command needs review.",
        "likely_files": [],
        "plan": ["Inspect README and the project manifest"],
        "questions": [],
        "risks": ["Partial response; verify the source before acting"],
        "format_warning": "Missing sections from model output",
    }
    monkeypatch.setattr(api, "analyze_task", lambda *args, **kwargs: (model_result, None))
    monkeypatch.setattr(api, "model_config", lambda: {
        "configured": True, "provider": "Gemma API (test)", "provider_id": "gemma_api", "model": "test-model"
    })

    response = client.post("/api/inspect", json={
        "source_type": "demo",
        "source": "",
        "task": "Review the README quick-start command and compare it with the actual entry point.",
        "use_ai": True,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    model_check = next(check for check in body["checks"] if check["id"] == "model")
    assert model_check["status"] == "partial"
    assert "partial or nonconforming" in model_check["detail"]
