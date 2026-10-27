from __future__ import annotations

import base64
import subprocess
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from app.services import repo_inspector as inspector


def _entry(path: str, content: str | bytes, sha: str | None = None) -> tuple[dict, dict]:
    raw = content.encode("utf-8") if isinstance(content, str) else content
    blob_sha = sha or path.replace("/", "-")
    entry = {"path": path, "type": "blob", "sha": blob_sha, "size": len(raw), "mode": "100644"}
    blob = {"encoding": "base64", "content": base64.b64encode(raw).decode("ascii"), "size": len(raw)}
    return entry, blob


def _fake_api(monkeypatch, entries, blobs, *, truncated=False, request_log=None, request_limit=None):
    calls = request_log if request_log is not None else []
    if request_limit is not None:
        monkeypatch.setattr(inspector, "GITHUB_API_MAX_REQUESTS", request_limit)

    def get_json(client, url, *, budget, max_response_bytes, params=None, not_found_message=""):
        if budget.get("remaining", 0) <= 0:
            raise inspector.GitHubSnapshotError("GitHub API request limit reached.", retryable=True)
        budget["remaining"] -= 1
        budget["used"] = budget.get("used", 0) + 1
        calls.append(url)
        if url.endswith("/repos/octo/project"):
            return {"full_name": "octo/project", "default_branch": "main"}
        if "/git/trees/" in url:
            if url.endswith("/git/trees/main"):
                return {"sha": "root-tree-sha", "tree": entries, "truncated": truncated}
            if url.endswith("/git/trees/root-tree-sha"):
                return {"sha": "root-tree-sha", "tree": [], "truncated": False}
            return {"tree": [], "truncated": False}
        if "/git/blobs/" in url:
            sha = url.rsplit("/", 1)[-1]
            if sha not in blobs:
                raise inspector.GitHubSnapshotError(not_found_message or "Blob not found.", not_found=True)
            return blobs[sha]
        raise AssertionError(f"Unexpected GitHub API URL: {url}")

    monkeypatch.setattr(inspector, "_github_api_get_json", get_json)
    return calls


def test_clone_timeout_triggers_api_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(inspector, "WORKSPACE", tmp_path / "workspace")
    seen = {}

    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(args=args[0], timeout=kwargs["timeout"])

    def fallback(owner, repo, target, priority_text=""):
        seen.update(owner=owner, repo=repo, target=target, priority_text=priority_text)
        snapshot = target / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "README.md").write_text("# Retrieved via API\n", encoding="utf-8")
        return snapshot, "GitHub API fallback snapshot of default branch main; bounded sample"

    monkeypatch.setattr(inspector.subprocess, "run", timeout)
    monkeypatch.setattr(inspector, "_fetch_github_api_snapshot", fallback)

    repo_id, root, label = inspector.prepare_repository(
        "github", "https://github.com/octo/project/issues/12",
        "Issue mentions README.md and the startup command.",
    )

    assert (root / "README.md").is_file()
    assert seen["owner"] == "octo" and seen["repo"] == "project"
    assert "startup command" in seen["priority_text"]
    assert "GitHub API fallback" in label
    assert not (inspector.WORKSPACE / repo_id / "checkout").exists()


def test_nonzero_clone_exit_triggers_api_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(inspector, "WORKSPACE", tmp_path / "workspace")
    monkeypatch.setattr(
        inspector.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stderr="network failed", stdout=""),
    )
    calls = []

    def fallback(owner, repo, target, priority_text=""):
        calls.append((owner, repo, priority_text))
        snapshot = target / "snapshot"
        snapshot.mkdir(parents=True, exist_ok=True)
        (snapshot / "src.py").write_text("print('fallback')\n", encoding="utf-8")
        return snapshot, "GitHub API fallback snapshot of default branch main; bounded sample"

    monkeypatch.setattr(inspector, "_fetch_github_api_snapshot", fallback)
    _, root, _ = inspector.prepare_repository(
        "github", "https://github.com/octo/project", "Look at src.py"
    )
    assert (root / "src.py").read_text(encoding="utf-8").startswith("print")
    assert calls == [("octo", "project", "Look at src.py")]


def test_api_fallback_inspects_small_repository_and_preserves_line_evidence(monkeypatch, tmp_path):
    entries, blobs = [], {}
    for path, content in [
        ("README.md", "# Project\n"),
        ("src/issue_handler.py", "def handle():\n    # BUG: incorrect state is returned\n    return False\n"),
        ("tests/test_issue_handler.py", "def test_issue_handler():\n    assert True\n"),
        ("package-lock.json", '{"generated": true}\n'),
        ("assets/logo.png", b"\x89PNG\r\n\x1a\n"),
        ("node_modules/dependency/index.js", "module.exports = 1;\n"),
        ("src/oversized.py", "x" * 2000),
    ]:
        entry, blob = _entry(path, content)
        entries.append(entry)
        blobs[entry["sha"]] = blob
    monkeypatch.setattr(inspector, "MAX_FILE_BYTES", 1000)
    monkeypatch.setattr(inspector, "MAX_TOTAL_BYTES", 5000)
    calls = _fake_api(monkeypatch, entries, blobs)

    root, note = inspector._fetch_github_api_snapshot(
        "octo", "project", tmp_path / "session", "Investigate src/issue_handler.py"
    )
    result = inspector.inspect_repository(root, "api-test", "API snapshot", "Investigate the incorrect state in src/issue_handler.py")

    assert (root / "src/issue_handler.py").is_file()
    assert (root / "tests/test_issue_handler.py").is_file()
    assert not (root / "package-lock.json").exists()
    assert not (root / "assets/logo.png").exists()
    assert not (root / "node_modules/dependency/index.js").exists()
    assert not (root / "src/oversized.py").exists()
    assert any(item["path"] == "src/issue_handler.py" and item["line"] == 2 for item in result["evidence"])
    fetched_blob_paths = [url.rsplit("/", 1)[-1] for url in calls if "/git/blobs/" in url]
    assert fetched_blob_paths[0] == "src-issue_handler.py"
    assert "default branch main" in note


def test_api_fallback_respects_file_and_total_byte_limits(monkeypatch, tmp_path):
    entries, blobs = [], {}
    for path, content in [
        ("README.md", "R" * 10),
        ("src/critical.py", "C" * 15),
        ("tests/test_critical.py", "T" * 12),
        ("src/other.py", "O" * 9),
    ]:
        entry, blob = _entry(path, content)
        entries.append(entry)
        blobs[entry["sha"]] = blob
    monkeypatch.setattr(inspector, "MAX_FILES", 3)
    monkeypatch.setattr(inspector, "MAX_FILE_BYTES", 50)
    monkeypatch.setattr(inspector, "MAX_TOTAL_BYTES", 25)
    _fake_api(monkeypatch, entries, blobs)

    root, note = inspector._fetch_github_api_snapshot(
        "octo", "project", tmp_path / "bounded",
        "The current issue explicitly points to src/critical.py",
    )
    included = [p for p in root.rglob("*") if p.is_file()]
    total_bytes = sum(p.stat().st_size for p in included)
    assert len(included) <= 3
    assert total_bytes <= 25
    assert (root / "src/critical.py").is_file()
    assert "bounded sample" in note


def test_api_fallback_prioritises_issue_mentioned_paths_over_generic_readme(monkeypatch, tmp_path):
    entries, blobs = [], {}
    for path, content in [
        ("README.md", "# Project\n"),
        ("src/keyboard_router.py", "def route_key():\n    return None\n"),
        ("src/other_module.py", "def other():\n    return 1\n"),
    ]:
        entry, blob = _entry(path, content)
        entries.append(entry)
        blobs[entry["sha"]] = blob
    calls = _fake_api(monkeypatch, entries, blobs)

    inspector._fetch_github_api_snapshot(
        "octo", "project", tmp_path / "priority",
        "Bug report: src/keyboard_router.py does not handle this keyboard shortcut.",
    )
    first_blob = next(url.rsplit("/", 1)[-1] for url in calls if "/git/blobs/" in url)
    assert first_blob == "src-keyboard_router.py"


def test_request_budget_limits_blob_calls_but_returns_honest_partial_snapshot(monkeypatch, tmp_path):
    entries, blobs = [], {}
    for idx in range(8):
        entry, blob = _entry(f"src/module_{idx}.py", f"def module_{idx}():\n    return {idx}\n")
        entries.append(entry)
        blobs[entry["sha"]] = blob
    calls = _fake_api(monkeypatch, entries, blobs, request_limit=5)

    root, note = inspector._fetch_github_api_snapshot("octo", "project", tmp_path / "budget", "Review modules")
    assert len([url for url in calls if "/git/blobs/" in url]) <= 3
    assert any(path.is_file() for path in root.rglob("*"))
    assert "bounded sample" in note
    assert "request budget reached" in note


def test_missing_repository_and_api_failure_clean_temporary_workspace(monkeypatch, tmp_path):
    workspace = tmp_path / "workspace"
    monkeypatch.setattr(inspector, "WORKSPACE", workspace)
    monkeypatch.setattr(
        inspector.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stderr="not found", stdout=""),
    )

    def missing(*args, **kwargs):
        raise inspector.GitHubSnapshotError(
            "GitHub repository was not found or is not publicly accessible.", not_found=True
        )

    monkeypatch.setattr(inspector, "_fetch_github_api_snapshot", missing)
    with pytest.raises(ValueError, match="not found or is not publicly accessible"):
        inspector.prepare_repository("github", "https://github.com/octo/project")
    assert not list(workspace.glob("*")) if workspace.exists() else True


@pytest.mark.parametrize(
    "failure",
    [
        inspector.GitHubSnapshotError("GitHub API rate limit reached.", retryable=True, rate_limited=True),
        inspector.GitHubSnapshotError("Timed out while reading GitHub repository data.", retryable=True),
    ],
)
def test_rate_limit_and_network_timeout_are_clear_and_do_not_leave_empty_success(monkeypatch, tmp_path, failure):
    monkeypatch.setattr(inspector, "WORKSPACE", tmp_path / "workspace")
    monkeypatch.setattr(
        inspector.subprocess, "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=128, stderr="network failed", stdout=""),
    )

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(inspector, "_fetch_github_api_snapshot", fail)
    with pytest.raises(ValueError, match="fallback failed"):
        inspector.prepare_repository("github", "https://github.com/octo/project")
    assert not list((tmp_path / "workspace").glob("*"))


def test_github_api_json_enforces_response_size_rate_limit_and_timeout():
    class Response:
        def __init__(self, status_code=200, headers=None, body=b"{}"):
            self.status_code = status_code
            self.headers = headers or {}
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def iter_bytes(self):
            yield self.body

    class Client:
        def __init__(self, response=None, error=None):
            self.response = response
            self.error = error

        def stream(self, *_args, **_kwargs):
            if self.error:
                raise self.error
            return self.response

    with pytest.raises(inspector.GitHubSnapshotError, match="safety limit"):
        inspector._github_api_get_json(
            Client(Response(headers={"Content-Length": "20"}, body=b'{"large":true}')),
            "https://api.github.com/test", budget={"remaining": 1},
            max_response_bytes=10,
        )
    with pytest.raises(inspector.GitHubSnapshotError, match="rate limit") as exc:
        inspector._github_api_get_json(
            Client(Response(status_code=403, headers={"X-RateLimit-Remaining": "0"})),
            "https://api.github.com/test", budget={"remaining": 1},
            max_response_bytes=100,
        )
    assert exc.value.rate_limited is True
    with pytest.raises(inspector.GitHubSnapshotError, match="Timed out"):
        inspector._github_api_get_json(
            Client(error=httpx.ReadTimeout("timeout")),
            "https://api.github.com/test", budget={"remaining": 1},
            max_response_bytes=100,
        )


def test_existing_local_repository_behaviour_is_unchanged(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "README.md").write_text("# Local repository\n", encoding="utf-8")
    repo_id, root, label = inspector.prepare_repository("local", str(source))
    try:
        assert (root / "README.md").read_text(encoding="utf-8") == "# Local repository\n"
        assert label == "Local snapshot: source"
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)
