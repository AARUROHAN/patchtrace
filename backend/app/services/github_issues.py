"""Safe, read-only helpers for public GitHub issue discovery."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

import httpx

API = "https://api.github.com"
HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "PatchTrace-open-source-contributor-assistant",
    "X-GitHub-Api-Version": "2022-11-28",
}


def parse_github_input(url: str) -> dict[str, Any] | None:
    """Accept a public repository URL or a direct issue URL; reject other GitHub paths."""
    parsed = urlparse(url.strip())
    if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
        return None
    parts = [part for part in parsed.path.strip("/").split("/") if part]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
        return None
    if len(parts) == 2:
        return {"owner": owner, "repo": repo, "issue_number": None}
    if len(parts) == 4 and parts[2] == "issues" and parts[3].isdigit():
        return {"owner": owner, "repo": repo, "issue_number": int(parts[3])}
    return None


def _normalise_issue(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "number": int(item.get("number") or 0),
        "title": str(item.get("title") or "Untitled issue")[:240],
        "body": str(item.get("body") or "")[:5000],
        "url": str(item.get("html_url") or ""),
        "state": str(item.get("state") or "open"),
        "labels": [
            str(label.get("name") or "")[:80]
            for label in (item.get("labels") or [])
            if isinstance(label, dict) and label.get("name")
        ][:8],
        "comments": int(item.get("comments") or 0),
    }


def fetch_issue(owner: str, repo: str, number: int) -> tuple[dict[str, Any] | None, str | None]:
    """Fetch one issue using GitHub's public, unauthenticated read API."""
    try:
        response = httpx.get(
            f"{API}/repos/{owner}/{repo}/issues/{number}",
            headers=HEADERS,
            timeout=12.0,
            follow_redirects=False,
        )
        if response.status_code == 404:
            return None, "GitHub could not find that public issue. Check the issue URL and repository visibility."
        if response.status_code == 403 or response.status_code == 429:
            return None, "GitHub issue lookup is temporarily rate-limited. Try again later."
        response.raise_for_status()
        item = response.json()
        if not isinstance(item, dict) or "pull_request" in item:
            return None, "That link is a pull request, not an issue. Please provide a /issues/NUMBER URL."
        return _normalise_issue(item), None
    except (httpx.HTTPError, ValueError):
        return None, "PatchTrace could not retrieve this issue from GitHub. Check the URL or try again."


def list_open_issues(owner: str, repo: str, limit: int = 10) -> tuple[list[dict[str, Any]], str | None]:
    """Return a small shortlist of open issues, excluding pull requests."""
    try:
        response = httpx.get(
            f"{API}/repos/{owner}/{repo}/issues",
            params={"state": "open", "per_page": min(max(limit, 1), 20), "sort": "created", "direction": "desc"},
            headers=HEADERS,
            timeout=12.0,
            follow_redirects=False,
        )
        if response.status_code == 404:
            return [], "No public issue list was available for this repository."
        if response.status_code == 403 or response.status_code == 429:
            return [], "GitHub issue lookup is temporarily rate-limited. You can still open a direct issue URL."
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, list):
            return [], "GitHub returned an unexpected issue list."
        issues = [
            _normalise_issue(item)
            for item in payload
            if isinstance(item, dict) and "pull_request" not in item
        ]
        return issues[:limit], None
    except (httpx.HTTPError, ValueError):
        return [], "PatchTrace could not load the public issues list. You can still paste a direct issue URL."
