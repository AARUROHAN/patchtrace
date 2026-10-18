from pathlib import Path
from app.services.repo_inspector import DEMO_REPO, inspect_repository, prepare_repository


def test_demo_inspection_finds_readme_and_python():
    repo_id, root, label = prepare_repository("demo", "")
    try:
        result = inspect_repository(root, repo_id, label, "README quick start command missing app.py")
        overview = result["overview"]
        assert overview["file_count"] >= 4
        assert "Python" in [x["name"] for x in overview["languages"]]
        assert "README.md" in overview["readme_files"]
        assert "src/patchtrace/cli.py" in overview["entry_points"]
        assert "LICENSE" in overview["license_files"]
        assert "tests" in overview["test_directories"]
        assert any(item["path"] == "README.md" for item in result["candidates"])
        assert any(item["path"] == "README.md" and item["line"] > 0 for item in result["evidence"])
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)


def test_local_source_requires_existing_directory(tmp_path):
    try:
        prepare_repository("local", str(tmp_path / "missing"))
    except ValueError as exc:
        assert "existing repository directory" in str(exc)
    else:
        raise AssertionError("Expected invalid local path to fail")


def test_github_source_rejects_non_github_url():
    try:
        prepare_repository("github", "https://example.com/owner/repo")
    except ValueError as exc:
        assert "https://github.com" in str(exc)
    else:
        raise AssertionError("Expected a non-GitHub URL to fail")
