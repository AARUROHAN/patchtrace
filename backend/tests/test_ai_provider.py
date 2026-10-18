import json

import httpx

from app.services.ai_provider import analyze_task, model_config


def test_gemma_api_config_never_exposes_secret(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "top-secret-test-value")
    config = model_config()
    assert config["configured"] is True
    assert config["provider_id"] == "gemma_api"
    assert config["model"] == "gemma-4-26b-a4b-it"
    assert "top-secret-test-value" not in repr(config)


def test_gemma_api_missing_key_fails_gracefully(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    result, error = analyze_task("Fix the setup docs command", {"all_paths": []}, [], [])
    assert result is None
    assert "GEMINI_API_KEY is missing" in error


def test_gemma_api_parses_response_and_filters_unknown_paths(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")
    expected = {
        "summary": "The README quick-start appears stale.",
        "likely_files": ["README.md", "invented.py"],
        "plan": ["Inspect README command", "Confirm the module entry point"],
        "questions": [],
        "risks": ["The command has not been run."],
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(expected)}]}}]}

    seen = {}

    def fake_post(url, *, headers, json, timeout):
        seen.update(url=url, headers=headers, payload=json, timeout=timeout)
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result, error = analyze_task(
        "Find the correct README command",
        {"all_paths": ["README.md", "src/app.py"], "file_count": 2},
        [{"path": "README.md", "score": 5}],
        [{"path": "README.md", "line": 12, "text": "python -m app", "relevance": 3}],
    )
    assert error is None
    assert result is not None
    assert result["likely_files"] == ["README.md"]
    assert result["provider"] == "Gemma API (Google AI Studio)"
    assert seen["headers"]["x-goog-api-key"] == "test-secret"
    assert "test-secret" not in seen["url"]
    assert seen["payload"]["generationConfig"]["responseMimeType"] == "application/json"


def test_gemma_api_rate_limit_returns_helpful_error(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret")

    class FakeResponse:
        status_code = 429

        def raise_for_status(self):
            return None

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task("Investigate a repository task", {"all_paths": []}, [], [])
    assert result is None
    assert "quota" in error.lower() or "rate limit" in error.lower()



def test_gemma_api_5xx_includes_provider_status_without_leaking_key(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    response = httpx.Response(
        status_code=503,
        json={"error": {"code": 503, "status": "UNAVAILABLE", "message": "Service temporarily overloaded"}},
        request=httpx.Request("POST", "https://generativelanguage.googleapis.com/v1beta/models/test:generateContent"),
    )
    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: response)

    result, error = analyze_task(
        "Trace the booking form handling",
        {"all_paths": ["booking.html"], "file_count": 1},
        [{"path": "booking.html", "score": 2}],
        [{"path": "booking.html", "line": 12, "text": "form", "relevance": 2}],
    )

    assert result is None
    assert "HTTP 503" in error
    assert "UNAVAILABLE" in error
    assert "temporarily overloaded" in error
    assert "test-secret-key" not in error
