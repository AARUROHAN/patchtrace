from pathlib import Path
from app.services.artifacts import make_artifact, propose_readme_diff, summarize_findings
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
        assert "What we found" in artifact
        assert "source line related to the task" in artifact
        assert "Files to look at first" in artifact
        assert "README.md" in artifact
        assert "Evidence to check" in artifact
        assert "Optional proposed change (not applied)" in artifact
        assert "A check that was not run is not a passing test." in artifact
        assert "Quick repository guide" in artifact
        assert "Regression test to add or update" in artifact
        assert "Suggested commands (not executed)" in artifact
        assert "Confidence and limitations" in artifact
        assert "python -m pytest -q" in artifact
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

 

def test_deterministic_summary_treats_todo_as_a_lead_not_a_confirmed_bug():
    analysis = {
        "overview": {"languages": [{"name": "Python"}], "entry_points": [], "manifest_files": [], "test_directories": []},
        "candidates": [{"path": "src/worker.py", "reason": "Contains a TODO marker."}],
        "evidence": [{"path": "src/worker.py", "line": 17, "text": "# TODO: handle retry failures"}],
    }
    summary = summarize_findings("Explore this repository for a contribution", analysis)
    assert "src/worker.py:17" in summary
    assert "maintenance marker" in summary
    assert "not proof of a bug" in summary


def test_deterministic_summary_is_honest_when_no_evidence_or_candidate_exists():
    analysis = {
        "overview": {"languages": [], "entry_points": [], "manifest_files": [], "test_directories": []},
        "candidates": [],
        "evidence": [],
    }
    summary = summarize_findings("Investigate the reported issue", analysis)
    assert "No convincing contribution opportunity" in summary
    assert "avoid proposing a fix" in summary
