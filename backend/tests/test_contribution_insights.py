from app.services.contribution_insights import build_fallback_insights


def test_vague_repository_task_does_not_promote_readme_to_a_bug():
    result = build_fallback_insights(
        task="tell what to investigate",
        overview={"test_directories": ["tests"], "all_paths": ["README.md"]},
        candidates=[{
            "path": "README.md",
            "score": 0,
            "reason": "Task keyword overlap + repository role; inspect the cited lines before editing.",
        }],
        evidence=[],
        issue=None,
        issue_candidates=[{"issue_number": 12}, {"issue_number": 13}],
    )

    assert "No specific defect can be concluded" in result["summary"]
    assert "navigation clue" in result["summary"]
    assert "2 open-issue candidate" in result["summary"]
    assert len(result["plan"]) == 3
    assert "paste its full /issues/<number> URL" in result["plan"][0]
    assert "Do not change the currently ranked file yet" in result["contribution"]
    assert "regression test" in result["contribution"]


def test_issue_fallback_uses_actual_source_evidence_and_test_surface():
    result = build_fallback_insights(
        task="Fix the parser when a value is empty",
        overview={"test_directories": ["tests/parser"], "entry_points": ["src/main.py"]},
        candidates=[{"path": "src/parser.py", "reason": "Contains the input parser"}],
        evidence=[{
            "path": "src/parser.py",
            "line": 42,
            "text": "if value:",
            "context": "40: value = request.value\n41: result = None\n42: if value:\n43: result = parse(value)",
            "relevance": 8,
        }],
        issue={
            "number": 99,
            "title": "Empty parser input is skipped",
            "body": "When value is empty, the parser should return an explicit validation error.",
        },
    )

    assert "Issue #99" in result["summary"]
    assert "src/parser.py:L42" in result["summary"]
    assert "if value:" in result["summary"]
    assert "tests/parser" in result["plan"][1]
    assert "expected behaviour in Issue #99" in result["contribution"]
    assert "before the fix" in result["contribution"]


def test_proposed_diff_fallback_explains_reviewable_documentation_change():
    result = build_fallback_insights(
        task="Fix documented setup command",
        overview={},
        candidates=[{"path": "README.md", "reason": "Documents setup"}],
        evidence=[],
        proposed_diff={"path": "README.md", "diff": "--- a/README.md\n+++ b/README.md"},
    )

    assert "concrete documentation mismatch" in result["summary"]
    assert "README.md" in result["contribution"]
    assert "has not been applied or verified" in result["contribution"]
