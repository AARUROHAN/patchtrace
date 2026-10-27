
from pathlib import Path
import httpx
import app.services.repo_inspector as inspector


class _FakeStream:
    def __init__(self, url):
        self.url = url
        self.status_code = 200 if url.endswith("/frontend/src/PagePopup.jsx") else 404

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def iter_bytes(self):
        if self.status_code == 200:
            yield b"export default function PagePopup() { return null; }\\n"


class _FakeClient:
    def __init__(self, **_):
        self.requests = []

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def stream(self, method, url):
        assert method == "GET"
        assert url.startswith("https://raw.githubusercontent.com/ToolJet/ToolJet/")
        return _FakeStream(url)


def test_raw_fallback_reads_exact_issue_path_without_tree_or_clone(monkeypatch, tmp_path):
    monkeypatch.setattr(httpx, "Client", _FakeClient)
    dest = tmp_path / "snapshot"
    count = inspector._github_raw_issue_snapshot(
        "ToolJet", "ToolJet", dest,
        "Bug in frontend/src/PagePopup.jsx resetting popup state",
    )
    assert count == 1
    assert (dest / "frontend/src/PagePopup.jsx").exists()
    assert not (dest / "README.md").exists()


def test_raw_fallback_rejects_path_traversal_and_unsupported_files(monkeypatch, tmp_path):
    requested = []

    class Recorder(_FakeClient):
        def stream(self, method, url):
            requested.append(url)
            return _FakeStream(url)

    monkeypatch.setattr(httpx, "Client", Recorder)
    dest = tmp_path / "snapshot"
    inspector._github_raw_issue_snapshot(
        "ToolJet", "ToolJet", dest,
        "Consider frontend/../secret.py and frontend/src/PagePopup.jsx and public/app.bin",
    )
    assert all("../" not in u and "secret.py" not in u and ".bin" not in u for u in requested)


def test_raw_fallback_is_bounded_and_rejects_oversized_response(monkeypatch, tmp_path):
    class OversizedStream(_FakeStream):
        def iter_bytes(self):
            if self.status_code == 200:
                yield b"x" * (inspector.MAX_FILE_BYTES + 1)

    class OversizedClient(_FakeClient):
        def stream(self, method, url):
            return OversizedStream(url)

    monkeypatch.setattr(httpx, "Client", OversizedClient)
    import pytest
    with pytest.raises(ValueError, match="no accessible supported"):
        inspector._github_raw_issue_snapshot(
            "ToolJet", "ToolJet", tmp_path / "snapshot",
            "Inspect frontend/src/PagePopup.jsx",
        )
