from pathlib import Path
from app.services.artifacts import make_artifact, propose_readme_diff
from app.services.repo_inspector import inspect_repository, prepare_repository


def test_demo_suggests_but_does_not_apply_readme_patch():
    repo_id, root, label = prepare_repository("demo", "")
    try:
        task = "The README quick-start command is stale because app.py is missing. Propose a documentation correction."
        analysis = inspect_repository(root, repo_id, label, task)
        original = (root / "README.md").read_text()
        diff = propose_readme_diff(root, analysis["overview"], task, analysis["candidates"])
        assert diff is not None
        assert "python app.py --help" in diff["diff"]
        assert "PYTHONPATH=src python -m patchtrace.cli --help" in diff["diff"]
        assert (root / "README.md").read_text() == original
        artifact = make_artifact(task, analysis, None, diff)
        assert "Evidence trail" in artifact
        assert "PROPOSED PATCH (NOT APPLIED)" in artifact.upper()
        assert "NOT_RUN" in artifact
    finally:
        import shutil
        shutil.rmtree(root.parent, ignore_errors=True)


def test_no_readme_patch_for_unrelated_task(tmp_path):
    (tmp_path / "README.md").write_text("# Example\n\nRun python app.py\n")
    task = "Add a unit test for the input parser."
    overview = {"readme_files": ["README.md"], "all_paths": ["README.md", "src/parser.py"]}
    assert propose_readme_diff(tmp_path, overview, task, []) is None
