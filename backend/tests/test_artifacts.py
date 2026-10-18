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
        assert "Analysis summary" in artifact
        assert "No usable AI summary was returned" in artifact
        assert "Relevant files to inspect" in artifact
        assert "README.md" in artifact
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



def test_artifact_includes_model_summary_and_model_file_shortlist(tmp_path):
    overview = {
        "source_label": "Public GitHub repository: example/project",
        "file_count": 2,
        "languages": [{"name": "Python", "count": 1}, {"name": "Markdown", "count": 1}],
        "entry_points": ["src/main.py"],
        "contributor_instructions": [],
        "test_directories": ["tests"],
        "truncated": False,
        "all_paths": ["README.md", "src/main.py"],
    }
    analysis = {
        "overview": overview,
        "candidates": [
            {"path": "README.md", "reason": "Documents the command that needs checking."},
            {"path": "src/main.py", "reason": "Contains the relevant entry point."},
        ],
        "evidence": [{"path": "README.md", "line": 18, "text": "python app.py --help", "context": "17: Quick start\\n18: python app.py --help", "relevance": 8}],
        "checks": [{"label": "Repository inventory", "status": "passed", "detail": "Scanned two files."}],
    }
    model_result = {
        "summary": "The README launch command appears inconsistent with the scanned entry point; verify it before editing.",
        "likely_files": ["README.md", "src/main.py"],
        "plan": ["Confirm the supported invocation", "Update the README", "Run the documented help command"],
        "questions": [],
        "risks": ["The command has not been executed"],
    }
    artifact = make_artifact("Investigate the quick-start command", analysis, model_result, None)
    assert model_result["summary"] in artifact
    assert "`README.md`" in artifact
    assert "`src/main.py`" in artifact
    assert "The command has not been executed" in artifact
