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


def make_artifact(task: str, analysis: dict[str, Any], model_result: dict[str, Any] | None, diff: dict[str, str] | None) -> str:
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
        task.strip()[:2500],
        "",
        "## What we found",
        "",
        summary or "The automatic summary was unavailable. Treat the file suggestions below as places to investigate, not confirmed bugs.",
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
