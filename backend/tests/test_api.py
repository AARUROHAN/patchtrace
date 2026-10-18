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
