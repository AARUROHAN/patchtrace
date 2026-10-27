from app.services import github_issues


def test_parse_github_repository_and_issue_urls():
    assert github_issues.parse_github_input("https://github.com/owner/project") == {
        "owner": "owner", "repo": "project", "issue_number": None
    }
    assert github_issues.parse_github_input("https://github.com/owner/project/issues") == {\n        "owner": "owner", "repo": "project", "issue_number": None\n    }\n    assert github_issues.parse_github_input("https://github.com/owner/project/issues/42") == {
        "owner": "owner", "repo": "project", "issue_number": 42
    }
    assert github_issues.parse_github_input("https://github.com/owner/project/pull/42") is None
    assert github_issues.parse_github_input("http://github.com/owner/project") is None


def test_open_issue_search_excludes_pull_requests(monkeypatch):
    class FakeResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return [
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

    monkeypatch.setattr(github_issues.httpx, "get", lambda *args, **kwargs: FakeResponse())
    issues, error = github_issues.list_open_issues("owner", "project")
    assert error is None
    assert len(issues) == 1
    assert issues[0]["number"] == 12
    assert issues[0]["labels"] == ["good first issue"]
