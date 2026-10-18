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
    schema = seen["payload"]["generationConfig"]["responseJsonSchema"]
    assert schema["type"] == "object"
    assert schema["required"] == ["summary", "likely_files", "plan", "questions", "risks"]
    assert schema["properties"]["plan"]["minItems"] == 1


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



def test_gemma_api_plain_prose_is_retained_with_warning(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "The repository contains a README setup instruction. Inspect the documented command, compare it with the available CLI entry point, and prepare a documentation-only fix if supported."}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Review the setup docs",
        {"all_paths": ["README.md", "src/patchtrace/cli.py"], "file_count": 2},
        [],
        [],
    )

    assert error is None
    assert result is not None
    assert "README setup instruction" in result["summary"]
    assert result["format_warning"]
    assert result["likely_files"] == []
    assert "test-secret-key" not in repr(result)
def test_gemma_api_plain_text_is_preserved_as_partial_analysis(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "Review booking.html and script.js. First inspect the required-field validation. Then check WhatsApp and email handling. The response is prose, not JSON."}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Review the booking flow",
        {"all_paths": ["booking.html", "script.js"], "file_count": 2},
        [],
        [],
    )

    assert error is None
    assert result is not None
    assert result["summary"].startswith("Review booking.html")
    assert result["likely_files"] == ["booking.html", "script.js"]
    assert "format_warning" in result
    assert "test-secret-key" not in repr(result)



def test_gemma_api_filters_thought_parts_and_disables_thinking(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")

    expected = {
        "summary": "The README documents the demo CLI command.",
        "likely_files": ["README.md"],
        "plan": ["Compare the documented command with the CLI entry point"],
        "questions": [],
        "risks": ["The command has not been executed"],
    }

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "candidates": [{
                    "content": {
                        "parts": [
                            {"text": "PRIVATE THOUGHTS SHOULD NEVER BE DISPLAYED", "thought": True},
                            {"text": json.dumps(expected)},
                        ]
                    }
                }]
            }

    seen = {}
    def fake_post(url, *, headers, json, timeout):
        seen["payload"] = json
        return FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)
    result, error = analyze_task(
        "Inspect the README command",
        {"all_paths": ["README.md", "src/patchtrace/cli.py"], "file_count": 2},
        [{"path": "README.md", "score": 3}],
        [{"path": "README.md", "line": 8, "text": "python app.py --help", "relevance": 3}],
    )

    assert error is None
    assert result is not None
    assert result["summary"] == expected["summary"]
    assert "PRIVATE THOUGHTS SHOULD NEVER BE DISPLAYED" not in repr(result)
    assert seen["payload"]["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "minimal"



def test_gemma_api_rejects_reasoning_style_commentary(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": "Goal: identify the command.\nTask: explain the issue.\nConstraints: use only evidence.\nLooking at the evidence, I need to re-read the lines.\nWait, let me re-read the evidence."}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Inspect the README quick start",
        {"all_paths": ["README.md"], "file_count": 1},
        [],
        [],
    )

    assert result is None
    assert error is not None
    assert "process commentary" in error.lower()
    assert "test-secret-key" not in error



def test_gemma_api_partial_json_is_retained_but_warned(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")

    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            # Deliberately omit plan, questions, and risks to model an incomplete response.
            partial = {
                "summary": "The README documents a stale CLI command; compare it with src/patchtrace/cli.py.",
                "likely_files": ["README.md", "invented.py"],
            }
            return {"candidates": [{"content": {"parts": [{"text": json.dumps(partial)}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Inspect the documented quick-start command",
        {"all_paths": ["README.md", "src/patchtrace/cli.py"], "file_count": 2},
        [{"path": "README.md", "score": 4}],
        [{"path": "README.md", "line": 20, "text": "python app.py --help", "relevance": 5}],
    )

    assert error is None
    assert result is not None
    assert result["summary"].startswith("The README documents a stale CLI command")
    assert result["likely_files"] == ["README.md"]
    assert result["plan"] == []
    assert result["questions"] == []
    assert result["risks"] == []
    assert "Partial structured response" in result["format_warning"]
    assert "test-secret-key" not in repr(result)



def test_gemma_api_unwraps_one_object_array(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    monkeypatch.setenv("GEMMA_API_MODEL", "gemma-4-26b-a4b-it")
    expected = {
        "summary": "The README command references a missing root script.",
        "likely_files": ["README.md"],
        "plan": ["Compare the README command with the CLI module", "Propose a documentation-only correction", "Verify the command manually"],
        "questions": [],
        "risks": ["The command has not been executed"],
    }

    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": json.dumps([expected])}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Inspect the README quick start",
        {"all_paths": ["README.md", "src/patchtrace/cli.py"], "file_count": 2},
        [{"path": "README.md", "score": 4}],
        [{"path": "README.md", "line": 20, "text": "python app.py --help", "relevance": 5}],
    )
    assert error is None
    assert result is not None
    assert result["summary"] == expected["summary"]
    assert result["plan"] == expected["plan"]
    assert not result.get("format_warning")


def test_gemma_api_malformed_json_is_not_rendered_as_summary(monkeypatch):
    monkeypatch.setenv("PATCHTRACE_AI_PROVIDER", "gemma_api")
    monkeypatch.setenv("GEMINI_API_KEY", "test-secret-key")
    malformed = (
        '[{"summary":"The README command is stale.",'
        '"likely_files":["README.md"],'
        '"plan":["Compare the documented command"],'
        '"Update README.md line 20 to use the module entry point.",'
        '"questions":[],"risks":[]}]'
    )

    class FakeResponse:
        status_code = 200
        def raise_for_status(self):
            return None
        def json(self):
            return {"candidates": [{"content": {"parts": [{"text": malformed}]}}]}

    monkeypatch.setattr(httpx, "post", lambda *args, **kwargs: FakeResponse())
    result, error = analyze_task(
        "Inspect the README quick start",
        {"all_paths": ["README.md"], "file_count": 1},
        [{"path": "README.md", "score": 4}],
        [{"path": "README.md", "line": 20, "text": "python app.py --help", "relevance": 5}],
    )
    assert result is None
    assert error is not None
    assert "malformed structured output" in error.lower()
    assert "README command is stale" not in error
