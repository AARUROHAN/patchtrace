import httpx
import pytest

from app.services import github_issues


def test_parse_github_repository_and_issue_urls():
    assert github_issues.parse_github_input("https://github.com/owner/project") == {
        "owner": "owner", "repo": "project", "issue_number": None
    }
    assert github_issues.parse_github_input("https://github.com/owner/project/issues") == {
        "owner": "owner", "repo": "project", "issue_number": None
    }
    assert github_issues.parse_github_input("https://github.com/owner/project/issues/42") == {
        "owner": "owner", "repo": "project", "issue_number": 42
    }
    assert github_issues.parse_github_input("https://github.com/owner/project/pull/42") is None
    assert github_issues.parse_github_input("http://github.com/owner/project") is None


def test_open_issue_search_excludes_pull_requests(monkeypatch):
    payload = [
        {
            "number": 12, "title": "Fix onboarding docs", "body": "The steps are outdated.",
            "html_url": "https://github.com/owner/project/issues/12", "state": "open",
            "labels": [{"name": "good first issue"}], "comments": 2,
        },
        {
            "number": 13, "title": "A pull request", "body": "Not an issue.",
            "html_url": "https://github.com/owner/project/pull/13", "state": "open",
            "pull_request": {"url": "https://api.github.com/repos/owner/project/pulls/13"},
        },
    ]
    seen = {}

    def fake_request(url, *, params=None, max_bytes=None):
        seen.update(url=url, params=params, max_bytes=max_bytes)
        return 200, {}, payload

    monkeypatch.setattr(github_issues, "_bounded_get_json", fake_request)
    issues, error = github_issues.list_open_issues("owner", "project")
    assert error is None
    assert len(issues) == 1
    assert issues[0]["number"] == 12
    assert issues[0]["labels"] == ["good first issue"]
    assert seen["params"]["per_page"] == 10
    assert seen["max_bytes"] == github_issues.MAX_ISSUE_LIST_RESPONSE_BYTES


def test_direct_issue_details_are_normalised_and_bounded(monkeypatch):
    payload = {
        "number": 42,
        "title": "A title " + ("x" * 500),
        "body": "Issue body " + ("body " * 2000),
        "html_url": "https://github.com/owner/project/issues/42",
        "state": "open",
        "labels": [{"name": "bug"}] * 20,
        "comments": 3,
    }
    monkeypatch.setattr(github_issues, "_bounded_get_json", lambda *args, **kwargs: (200, {}, payload))
    issue, error = github_issues.fetch_issue("owner", "project", 42)
    assert error is None
    assert issue["number"] == 42
    assert len(issue["title"]) <= 240
    assert len(issue["body"]) <= 5000
    assert len(issue["labels"]) <= 8
    assert issue["url"] == payload["html_url"]


@pytest.mark.parametrize(
    ("status", "headers", "expected"),
    [
        (404, {}, "could not find that public issue"),
        (403, {"X-RateLimit-Remaining": "0"}, "rate-limited"),
        (429, {}, "rate-limited"),
        (503, {}, "HTTP 503"),
    ],
)
def test_issue_lookup_handles_http_errors(monkeypatch, status, headers, expected):
    monkeypatch.setattr(
        github_issues,
        "_bounded_get_json",
        lambda *args, **kwargs: (status, headers, None),
    )
    issue, error = github_issues.fetch_issue("owner", "project", 42)
    assert issue is None
    assert expected.lower() in error.lower()


def test_issue_lookup_handles_network_timeout(monkeypatch):
    def timeout(*args, **kwargs):
        raise httpx.ReadTimeout("timeout")

    monkeypatch.setattr(github_issues, "_bounded_get_json", timeout)
    issue, error = github_issues.fetch_issue("owner", "project", 42)
    assert issue is None
    assert "timed out" in error.lower() or "retry" in error.lower()


def test_issue_response_body_limit_is_enforced(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self):
            yield b'{"body":"1234567890"}'

    class FakeClient:
        def __init__(self, **kwargs):
            self.options = kwargs

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(github_issues.httpx, "Client", FakeClient)
    with pytest.raises(ValueError, match="safety limit"):
        github_issues._bounded_get_json(
            "https://api.github.com/repos/owner/project/issues/42",
            max_bytes=10,
        )


def test_issue_response_declared_size_is_rejected_before_reading(monkeypatch):
    class FakeResponse:
        status_code = 200
        headers = {"Content-Length": "200"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self):
            raise AssertionError("Oversized response should be rejected from its header.")

    class FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def stream(self, *_args, **_kwargs):
            return FakeResponse()

    monkeypatch.setattr(github_issues.httpx, "Client", FakeClient)
    with pytest.raises(ValueError, match="safety limit"):
        github_issues._bounded_get_json(
            "https://api.github.com/repos/owner/project/issues/42",
            max_bytes=10,
        )
