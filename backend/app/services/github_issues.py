"""Safe, read-only helpers for public GitHub issue discovery."""
from __future__ import annotations

import json
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
ISSUE_LOOKUP_TIMEOUT = httpx.Timeout(connect=4.0, read=12.0, write=4.0, pool=4.0)
MAX_ISSUE_RESPONSE_BYTES = 1_000_000
MAX_ISSUE_LIST_RESPONSE_BYTES = 2_000_000


def _bounded_get_json(
    url: str,
    *,
    params: dict[str, Any] | None = None,
    max_bytes: int = MAX_ISSUE_RESPONSE_BYTES,
) -> tuple[int, dict[str, str], Any]:
    """Fetch a public GitHub API response without buffering an unbounded issue body."""
    with httpx.Client(headers=HEADERS, timeout=ISSUE_LOOKUP_TIMEOUT, follow_redirects=False) as client:
        with client.stream("GET", url, params=params) as response:
            status = int(response.status_code)
            headers = dict(response.headers)
            if status != 200:
                return status, headers, None

            content_length = response.headers.get("Content-Length")
            if content_length:
                try:
                    if int(content_length) > max_bytes:
                        raise ValueError("GitHub returned an issue response larger than PatchTrace's safety limit.")
                except (TypeError, ValueError) as exc:
                    if isinstance(exc, ValueError) and "safety limit" in str(exc):
                        raise

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                total += len(chunk)
                if total > max_bytes:
                    raise ValueError("GitHub returned an issue response larger than PatchTrace's safety limit.")
                chunks.append(chunk)
            try:
                payload = json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ValueError("GitHub returned an invalid issue response.") from exc
            return status, headers, payload


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
    if len(parts) == 2 or (len(parts) == 3 and parts[2] == "issues"):
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
        status, headers, item = _bounded_get_json(
            f"{API}/repos/{owner}/{repo}/issues/{number}",
            max_bytes=MAX_ISSUE_RESPONSE_BYTES,
        )
        if status == 404:
            return None, "GitHub could not find that public issue. Check the issue URL and repository visibility."
        if status in {403, 429}:
            remaining = str(headers.get("X-RateLimit-Remaining", "")).strip()
            if status == 429 or remaining == "0" or headers.get("Retry-After"):
                return None, "GitHub issue lookup is temporarily rate-limited. Try again later."
            return None, "GitHub denied access to this issue. Check that the repository and issue are public."
        if status != 200:
            return None, f"GitHub issue lookup failed (HTTP {status}). Try again later."
        if not isinstance(item, dict) or "pull_request" in item:
            return None, "That link is a pull request, not an issue. Please provide a /issues/NUMBER URL."
        return _normalise_issue(item), None
    except (httpx.HTTPError, ValueError):
        return None, "PatchTrace could not retrieve this issue from GitHub. It may be too large or the request may have timed out; check the URL and retry."


def list_open_issues(owner: str, repo: str, limit: int = 10) -> tuple[list[dict[str, Any]], str | None]:
    """Return a small shortlist of open issues, excluding pull requests."""
    bounded_limit = min(max(int(limit), 1), 20)
    try:
        status, headers, payload = _bounded_get_json(
            f"{API}/repos/{owner}/{repo}/issues",
            params={"state": "open", "per_page": bounded_limit, "sort": "created", "direction": "desc"},
            max_bytes=MAX_ISSUE_LIST_RESPONSE_BYTES,
        )
        if status == 404:
            return [], "No public issue list was available for this repository."
        if status in {403, 429}:
            remaining = str(headers.get("X-RateLimit-Remaining", "")).strip()
            if status == 429 or remaining == "0" or headers.get("Retry-After"):
                return [], "GitHub issue lookup is temporarily rate-limited. You can still paste a direct issue URL."
            return [], "GitHub denied access to the issue list. Check that the repository is public."
        if status != 200:
            return [], f"GitHub issue lookup failed (HTTP {status}). You can still paste a direct issue URL."
        if not isinstance(payload, list):
            return [], "GitHub returned an unexpected issue list."
        issues = [
            _normalise_issue(item)
            for item in payload
            if isinstance(item, dict) and "pull_request" not in item
        ]
        return issues[:bounded_limit], None
    except (httpx.HTTPError, ValueError):
        return [], "PatchTrace could not load the public issues list. The response may be too large or the request may have timed out; you can still paste a direct issue URL."
