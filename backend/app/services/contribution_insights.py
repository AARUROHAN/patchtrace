"""Evidence-backed fallback insights for PatchTrace when no model answer is available."""
from __future__ import annotations

import re
from typing import Any

_GENERIC_TASKS = {
    "what to investigate", "tell what to investigate", "what should we work on",
    "investigate an issue", "explore opportunities", "explore this repository",
    "help me find an issue", "look into this",
}
_TASK_STOPWORDS = {
    "what", "where", "when", "why", "how", "tell", "me", "we", "our", "you",
    "the", "a", "an", "to", "on", "for", "with", "about", "please", "help",
    "investigate", "investigation", "work", "should", "could", "would", "find",
    "look", "into", "this", "that", "issue", "repo", "repository", "something",
}


def _is_underspecified(task: str) -> bool:
    normalized = re.sub(r"\s+", " ", task.lower()).strip(" .!?")
    if normalized in {item.strip(" .!?") for item in _GENERIC_TASKS}:
        return True
    words = re.findall(r"[a-z0-9_]+", normalized)
    meaningful = [word for word in words if word not in _TASK_STOPWORDS and len(word) > 2]
    return len(meaningful) < 2


def _line_ref(item: dict[str, Any]) -> str:
    path = str(item.get("path", "unknown file"))
    line = item.get("line")
    return f"{path}:L{line}" if isinstance(line, int) else path


def _quoted_evidence(item: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", str(item.get("text", ""))).strip()
    if not text:
        return _line_ref(item)
    if len(text) > 180:
        text = text[:177].rstrip() + "..."
    return f"{_line_ref(item)} contains “{text}”"


def build_fallback_insights(
    task: str,
    overview: dict[str, Any],
    candidates: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    issue: dict[str, Any] | None = None,
    issue_candidates: list[dict[str, Any]] | None = None,
    proposed_diff: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return useful, source-grounded card text without pretending a bug was verified."""
    issue_candidates = issue_candidates or []
    test_dirs = overview.get("test_directories") or []
    candidate = next((item for item in candidates if item.get("path")), None)
    first_evidence = next((item for item in evidence if item.get("path") and item.get("text")), None)
    issue_title = str((issue or {}).get("title") or "").strip()
    issue_number = (issue or {}).get("number") or (issue or {}).get("issue_number")
    issue_body = re.sub(r"\s+", " ", str((issue or {}).get("body") or "")).strip()[:360]
    vague_task = _is_underspecified(task) and issue is None

    if proposed_diff and proposed_diff.get("path"):
        summary = (
            f"The scanner found a concrete documentation mismatch in {proposed_diff['path']} and generated a proposed diff. "
            "That proposal is reviewable but has not been applied or executed; inspect the surrounding setup instructions and confirm the replacement command is supported before accepting it."
        )
    elif vague_task:
        if candidate:
            candidate_path = candidate["path"]
            reason = str(candidate.get("reason") or "it ranked highly in the bounded scan").strip()
            ranked_note = f"{candidate_path} ranked first because {reason[0].lower() + reason[1:] if reason else 'it ranked highly in the scan'}"
        else:
            ranked_note = "the scan did not find a high-confidence source candidate"
        issue_note = (
            f"The repository scan returned {len(issue_candidates)} open-issue candidate(s); select one from the issue list below to get issue-specific analysis."
            if issue_candidates
            else "No open-issue candidates were returned in this run; paste a direct GitHub issue URL or give a concrete expected-versus-actual behaviour."
        )
        summary = (
            f"No specific defect can be concluded from the task “{task.strip()[:90]}”: it does not identify an observable behaviour or a particular issue. "
            f"{ranked_note}, which is a navigation clue—not evidence that the file contains a bug. {issue_note}"
        )
    elif issue and first_evidence:
        issue_label = f"Issue #{issue_number} “{issue_title}”" if issue_number else f"Issue “{issue_title}”"
        summary = (
            f"{issue_label} reports: “{(issue_body or issue_title)[:220]}”. "
            f"The strongest matching source evidence in the bounded scan is {_quoted_evidence(first_evidence)}. "
            "This gives us a specific place to trace against the reported expected behaviour, but a matching line alone does not prove it causes the issue."
        )
    elif issue:
        issue_label = f"Issue #{issue_number} “{issue_title}”" if issue_number else f"Issue “{issue_title}”"
        paths = ", ".join(item["path"] for item in candidates[:3] if item.get("path")) or "no source paths"
        summary = (
            f"{issue_label} describes a concrete report, but the scanned snapshot did not produce a line-level match for it. "
            f"The current candidates ({paths}) are leads based on path/task relevance, not a confirmed root cause. "
            "The next useful step is to inspect the issue-named files directly or widen the scan before proposing a code change."
        )
    elif first_evidence:
        summary = (
            f"The strongest concrete lead for “{task.strip()[:100]}” is {_quoted_evidence(first_evidence)}. "
            f"The scan ranked this area for inspection{(' because ' + str(candidate.get('reason'))) if candidate else ''}. "
            "Treat it as a hypothesis: trace the surrounding logic and reproduce the stated behaviour before calling it a defect."
        )
    elif candidate:
        summary = (
            f"The scan could not tie “{task.strip()[:100]}” to a specific source line. "
            f"{candidate['path']} ranked highest because {str(candidate.get('reason') or 'it matched the bounded scan')}. "
            "That is only a lead; the current evidence is insufficient to name a root cause or justify a patch."
        )
    else:
        summary = (
            f"The scan did not find line-level evidence for “{task.strip()[:100]}”. "
            "There is not enough repository evidence to identify a likely cause safely. "
            "Narrow the task to one observable behaviour or provide the exact issue URL before choosing a fix."
        )

    if vague_task:
        plan = [
            "Choose a specific open issue from the shortlist below, or paste its full /issues/<number> URL. A repository URL alone does not identify which behaviour is broken.",
            "For that issue, compare its expected and actual behaviour with cited source lines; follow the relevant call/config path rather than editing the README just because it ranked first.",
            "Re-run inspection with the concrete issue and add a regression test for its exact reported behaviour before proposing a minimal fix. If the issue cannot be reproduced, do not present a code change as a fix.",
        ]
        contribution = (
            "Do not change the currently ranked file yet: the task did not identify a defect, so a code patch would be guesswork. "
            "The strongest contribution path is issue-first—select a real open issue, reproduce its expected-versus-actual behaviour, add a failing regression test, and then change only the source responsible. "
            "This run has not established a root cause."
        )
    else:
        first_path = str(first_evidence.get("path")) if first_evidence else str(candidate.get("path")) if candidate else None
        first_line = first_evidence.get("line") if first_evidence else None
        ref = f"{first_path}:L{first_line}" if first_path and isinstance(first_line, int) else first_path or "the highest-ranked candidate"
        if first_path and first_evidence:
            plan_first = f"Open {ref} and read its captured neighbouring lines. Trace the function, condition, configuration or documented command that determines this behaviour; write down what the issue says should happen versus what the code does."
        elif first_path:
            plan_first = f"Inspect {first_path} and validate why it was ranked. The scan did not cite a matching line, so locate the actual implementation path before inferring a cause."
        else:
            plan_first = "Narrow the report to one observable behaviour, then locate the implementation path before proposing a change."
        test_target = test_dirs[0] if test_dirs else "the nearest existing test file (no test directory was identified)"
        plan = [
            plan_first,
            f"Inspect {test_target} for an assertion covering the reported behaviour. If none exists, define one precise expected outcome from the task/issue before writing a regression test.",
            "Reproduce the current behaviour, run the focused test to establish a baseline, then make the smallest change. Re-run that test and applicable project checks; only report a pass when the command actually passes.",
        ]
        if proposed_diff and proposed_diff.get("path"):
            contribution = (
                f"Review the proposed diff for {proposed_diff['path']} and verify its replacement against the repository's real entry point or supported command. "
                "Keep the PR limited to this documented mismatch and add/adjust a lightweight check if the repository already has a suitable documentation test. The generated diff has not been applied or verified."
            )
        elif issue and first_path:
            contribution = (
                f"Potential focused PR: add a regression test in {test_target} that captures the expected behaviour in Issue #{issue_number or 'the reported issue'}; then make a minimal change around {ref} only if tracing confirms this code is responsible. "
                "The acceptance criterion is that the test reproduces the reported failure before the fix and passes afterwards."
            )
        elif first_path:
            contribution = (
                f"A credible contribution would pair a regression test for the task's stated expected behaviour with a minimal change in {first_path}—but only if reproducing the problem confirms this path is responsible. "
                f"Use {test_target} for the test if it is the repository's applicable suite; otherwise extend the nearest existing test. The current scan has not verified a fix."
            )
        else:
            contribution = (
                "No safe patch is justified yet. First reproduce the reported behaviour and locate its implementation and test surface. If it is reproducible, contribute a regression test plus the smallest implementation change; otherwise improve the reproduction details rather than guessing at source edits."
            )

    return {"summary": summary[:1200], "plan": plan[:3], "contribution": contribution[:1200]}
