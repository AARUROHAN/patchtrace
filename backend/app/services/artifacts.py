from __future__ import annotations

import difflib
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def propose_readme_diff(root: Path, overview: dict[str, Any], task: str, candidates: list[dict[str, Any]]) -> dict[str, str] | None:
    task_low = task.lower()
    if not any(word in task_low for word in ("readme", "documentation", "docs", "quick-start", "quick start", "command", "setup")):
        return None
    readme_path = next((p for p in overview.get("readme_files", []) if p.lower().startswith("readme")), None)
    if not readme_path:
        return None
    path = root / readme_path
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    changed = original
    available = set(overview.get("all_paths", []))
    # Deliberately narrow, evidence-backed rewrite: only replace the known demo's stale entry point.
    if "python app.py --help" in changed and "app.py" not in available and "src/patchtrace/cli.py" in available:
        changed = changed.replace("python app.py --help", "PYTHONPATH=src python -m patchtrace.cli --help", 1)
    if changed == original:
        return None
    diff = "".join(difflib.unified_diff(original.splitlines(keepends=True), changed.splitlines(keepends=True), fromfile=f"a/{readme_path}", tofile=f"b/{readme_path}"))
    return {"path": readme_path, "diff": diff, "status": "proposed_not_applied"}


def summarize_findings(task: str, analysis: dict[str, Any]) -> str:
    """Give an honest plain-language summary when the model is disabled or unavailable."""
    evidence = analysis.get("evidence") or []
    candidates = analysis.get("candidates") or []
    if evidence:
        item = evidence[0]
        path = str(item.get("path") or "the inspected source")
        try:
            line = int(item.get("line") or 0)
        except (TypeError, ValueError):
            line = 0
        reference = f"`{path}:{line}`" if line > 0 else f"`{path}`"
        source_text = str(item.get("text") or "")
        markers = ("TODO", "FIXME", "HACK", "BUG", "WORKAROUND", "XXX", "DEPRECATED")
        marker = next((word for word in markers if word in source_text.upper()), None)
        if marker:
            return (
                f"The scan found a {marker} maintenance marker at {reference}. "
                "That is a lead to investigate, not proof of a bug. Check the surrounding code and existing tests "
                "to decide whether a useful contribution is still needed."
            )
        return (
            f"The scan found a source line related to the task at {reference}. "
            "This does not confirm the reported issue. Compare it with surrounding code and related tests, "
            "then reproduce the behaviour before choosing a small fix."
        )
    if candidates:
        first = candidates[0]
        path = str(first.get("path") or "the highest-ranked source file")
        reason = str(first.get("reason") or "The file was ranked as a possible place to investigate.")
        return (
            f"No direct line-level evidence confirmed the report. Start with `{path}`: {reason} "
            "Treat this as a lead, inspect related tests or callers, and reproduce the problem before suggesting a change."
        )
    return (
        "No convincing contribution opportunity was identified in the inspected snapshot. "
        "Review the project's contribution guide and entry points, inspect the relevant source and tests more broadly, "
        "and avoid proposing a fix until there is a reproducible gap."
    )


def make_artifact(task: str, analysis: dict[str, Any], model_result: dict[str, Any] | None, diff: dict[str, str] | None, issue: dict[str, Any] | None = None) -> str:
    """Build a one-page, plain-language contribution summary without dashboard statistics."""
    overview = analysis["overview"]
    evidence = analysis.get("evidence", [])
    candidates = analysis.get("candidates", [])
    summary = (model_result or {}).get("summary")
    plan = (model_result or {}).get("plan") or [
        "Read the issue and confirm the reported behaviour in the source.",
        "Check the related code and any nearby test before choosing a fix.",
        "Make the smallest change that addresses the issue, then run a relevant check.",
    ]
    lines = [
        "# PatchTrace — Contribution Summary",
        "",
        f"**Repository:** {overview['source_label']}",
        "",
        "## What problem are we looking at?",
        "",
    ]
    if issue:
        lines += [
            f"**GitHub issue:** [#{issue.get('number', '')} — {issue.get('title', 'Issue')}]({issue.get('url', '')})",
            "",
            (issue.get("body") or "No description was provided in the issue.")[:1200],
            "",
        ]
    else:
        lines += [task.strip()[:1500], ""]
    lines += [
        "## Quick repository guide",
        "",
        f"- **Languages:** {', '.join(item['name'] for item in overview.get('languages', [])[:5]) or 'Not identified from the inspected files'}",
        f"- **Entry points:** {', '.join(overview.get('entry_points', [])[:3]) or 'Not identified'}",
        f"- **Project setup:** {', '.join(overview.get('manifest_files', [])[:3]) or 'No supported project manifest was identified'}",
        f"- **Test setup:** {', '.join(overview.get('test_directories', [])[:3]) or 'No test directory was identified'}",
        "",
        "## What we found",
        "",
        summary or summarize_findings(task, analysis),
        "",
        "## What to investigate next",
        "",
    ]
    for item in plan[:3]:
        lines.append(f"- {item}")
    paths_to_show = (model_result or {}).get("likely_files") or [
        item.get("path", "") for item in candidates if item.get("path")
    ]
    allowed_paths = set(overview.get("all_paths") or paths_to_show)
    paths_to_show = [path for path in paths_to_show if path in allowed_paths][:2]
    lines += ["", "## Files to look at first", ""]
    if paths_to_show:
        lines.extend(f"- `{path}`" for path in paths_to_show)
    else:
        lines.append("- No source file could be prioritised yet.")
    lines += ["", "## What we could contribute", ""]
    if diff:
        lines.append(f"A small documentation change is proposed in `{diff['path']}`. It has not been applied; review it before using it.")
    elif candidates:
        first = candidates[0]
        lines.append(
            f"Start by reviewing `{first.get('path', 'the suggested source file')}`. "
            f"{first.get('reason', 'Confirm the cause before deciding on a change')} "
            "If the issue is reproducible, contribute a focused fix or a regression test."
        )
    else:
        lines.append("There is not enough evidence to recommend a safe code change yet. Confirm the problem and identify the relevant source before editing.")
    lines += ["", "## Regression test to add or update", ""]
    test_dirs = overview.get("test_directories", [])
    if test_dirs:
        lines.append(
            f"Add a focused test under {', '.join(test_dirs[:2])} that reproduces the reported behaviour. "
            "It should fail before the fix and pass after it; do not rely on a generic smoke test alone."
        )
    else:
        lines.append(
            "No test directory was identified in this snapshot. Check the project's own contribution guide and test configuration, "
            "then add a focused regression test beside the nearest related tests."
        )

    lines += ["", "## Suggested commands (not executed)", ""]
    suggested_checks = [
        check for check in analysis.get("checks", [])
        if check.get("id") not in {"inventory", "model"}
        and check.get("type") not in {"static", "model"}
        and isinstance(check.get("command"), str)
        and check["command"].strip()
    ][:4]
    if suggested_checks:
        for check in suggested_checks:
            status = str(check.get("status") or "not_run").upper().replace("_", " ")
            detail = str(check.get("detail") or "Suggested only; no result was recorded.").replace("\\n", " ").strip()
            lines += [
                f"- **{check.get('label', 'Check')} — {status}:** `{check['command']}`",
                f"  {detail[:220]}",
            ]
    else:
        lines.append(
            "No reliable test command could be inferred from the inspected files. Follow the repository's own setup instructions "
            "and confirm the command before running it."
        )
    lines += ["", "## Evidence to check", ""]
    if evidence:
        for item in evidence[:3]:
            lines.append(f"- `{item['path']}:{item['line']}` — {item['text']}")
    else:
        lines.append("- No direct line-level evidence was found for this run. Check the source manually before proposing a fix.")
    risks = (model_result or {}).get("risks") or []
    if risks:
        lines += ["", f"**One thing to watch:** {risks[0]}", ""]
    questions = (model_result or {}).get("questions") or []
    if questions:
        lines += ["", "## One thing to clarify", "", f"- {questions[0]}"]
    lines += ["", "## Confidence and limitations", ""]
    if evidence:
        confidence = "Provisional: the snapshot contains line-level source references, but the issue still needs reproduction or a relevant check."
    else:
        confidence = "Low: no direct line-level evidence was found. Investigate the related source and reproduce the report before proposing a fix."
    if overview.get("truncated"):
        confidence += " This was a bounded snapshot, so relevant files may be missing."
    lines.append(confidence)
    if model_result:
        lines.append("AI recommendations are hypotheses; paths are checked against the inspected snapshot, not proof that the reported behaviour is a bug.")
    lines += [
        "",
        "## Before contributing",
        "",
        "- This is a suggested direction, not a confirmed bug or approved patch.",
        "- No change has been applied to the repository.",
        "- Tests are only passed when PatchTrace records an actual executed check.",
        "- A check that was not run is not a passing test.",
        "",
    ]
    if diff:
        lines += [
            "## Optional proposed change (not applied)",
            "",
            "```diff",
            diff["diff"].rstrip(),
            "```",
            "",
        ]
    return "\n".join(lines)
