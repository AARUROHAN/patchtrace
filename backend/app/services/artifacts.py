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
    overview = analysis["overview"]
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    evidence = analysis.get("evidence", [])
    plan = (model_result or {}).get("plan") or [
        "Re-read the issue and confirm the observed behaviour against the repository evidence.",
        "Inspect the highest-ranked candidate file and its callers or related tests before editing.",
        "Create a minimal, issue-scoped change; avoid unrelated refactors.",
        "Add or update a focused regression test when the repository has a suitable test harness.",
        "Run an appropriate check and attach the actual output to the contribution description.",
    ]
    lines = [
        "# PatchTrace — Contribution Brief", "", f"**Generated:** {now}", f"**Repository:** {overview['source_label']}",
        f"**Task / issue:** {task.strip()}", "", "> Status: this is a suggested contribution artifact. It is not an approved patch, merge, or proof that tests passed.",
        "", "## Repository snapshot", "", f"- Supported files scanned: {overview['file_count']}",
        f"- Languages detected: {', '.join(i['name'] for i in overview['languages']) or 'No language could be inferred'}",
        f"- Entry points: {', '.join(overview['entry_points']) or 'Not identified'}",
        f"- Contributor guidance: {', '.join(overview['contributor_instructions'][:4]) or 'No dedicated contributor guidance found'}",
        f"- Test directories: {', '.join(overview['test_directories']) or 'Not identified'}",
        f"- Snapshot truncated: {'yes' if overview['truncated'] else 'no'}", "", "## Evidence trail", "",
    ]
    if evidence:
        for item in evidence[:10]:
            lines.append(f"- `{item['path']}:{item['line']}` — {item['text']}")
    else:
        lines.append("- No high-confidence line-level evidence was found. Inspect the repository manually before editing.")
    lines += ["", "## Proposed plan", ""]
    for idx, item in enumerate(plan[:8], 1):
        lines.append(f"{idx}. {item}")
    if (model_result or {}).get("questions"):
        lines += ["", "## Questions before implementation", ""]
        lines += [f"- {q}" for q in model_result["questions"][:5]]
    if (model_result or {}).get("risks"):
        lines += ["", "## Risks to review", ""]
        lines += [f"- {r}" for r in model_result["risks"][:5]]
    lines += ["", "## Verification ledger", "", "| Check | State at generation | Notes |", "|---|---|---|"]
    for check in analysis.get("checks", []):
        lines.append(f"| {check['label']} | {check['status'].upper()} | {check['detail'].replace('|', '/')} |")
    lines += ["", "## Limitations", "", "- File ranking is a heuristic and may miss relevant code paths.",
              "- Model suggestions, if enabled, are advisory and must be reviewed against the cited files.",
              "- A planned command is not a completed check. Only the verification ledger records executed results.",
              "- This brief does not certify the contribution as correct, secure, or maintainable.", ""]
    if diff:
        lines += ["## Proposed patch (not applied)", "", "```diff", diff["diff"].rstrip(), "```", "",
                  f"The proposed change is limited to `{diff['path']}` and has not modified the inspected repository.", ""]
    return "\n".join(lines)
