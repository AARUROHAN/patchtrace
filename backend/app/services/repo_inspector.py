from __future__ import annotations

import base64
import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
import httpx
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.services.github_issues import parse_github_input

ROOT = Path(__file__).resolve().parents[3]
DEMO_REPO = ROOT / "demo_repo"
WORKSPACE = ROOT / "backend" / ".patchtrace" / "workspaces"
SKIP_DIRS = {
    ".git", ".next", "node_modules", "dist", "build", "coverage", ".venv", "venv",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".tox", "vendor", "target", ".idea",
}
MAX_FILES = int(os.getenv("PATCHTRACE_MAX_FILES", "500"))
MAX_FILE_BYTES = int(os.getenv("PATCHTRACE_MAX_FILE_BYTES", "600000"))
MAX_TOTAL_BYTES = 12_000_000
TEXT_EXTS = {
    ".py", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh", ".ini", ".cfg",
    ".go", ".rs", ".java", ".rb", ".sql",
    # Native source extensions are essential for C/C++ and mixed-language monorepos.
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm",
    ".cmake", ".gradle", ".xml", ".properties", ".swift", ".kt", ".kts", ".cs",
    ".mjs", ".cjs", ".vue", ".svelte", ".php", ".dart", ".ex", ".exs", ".erl",
    ".hrl", ".hs", ".lhs", ".pl", ".pm", ".r", ".rmd", ".jl", ".lua", ".scala",
    ".sc", ".clj", ".cljs", ".edn", ".sol", ".tf", ".hcl", ".proto", ".graphql",
    ".gql", ".astro", ".inc", ".csproj", ".sln", ".fsproj", ".vbproj",
}
STOPWORDS = {"this", "that", "with", "from", "have", "what", "when", "where", "which", "would", "could", "should", "about", "into", "file", "files", "issue", "task", "please", "help", "make", "need", "does", "not", "and", "the", "for", "are", "was", "were", "you", "your", "our", "can", "how", "why", "has", "its", "readme", "repo", "repository"}


GITHUB_API = "https://api.github.com"
GITHUB_API_HEADERS = {
    "Accept": "application/vnd.github+json",
    "User-Agent": "PatchTrace-open-source-contributor-assistant",
    "X-GitHub-Api-Version": "2022-11-28",
}
GITHUB_API_TIMEOUT_SECONDS = min(20.0, max(2.0, float(os.getenv("PATCHTRACE_GITHUB_API_TIMEOUT_SECONDS", "9"))))
GITHUB_API_MAX_REQUESTS = min(55, max(4, int(os.getenv("PATCHTRACE_GITHUB_API_MAX_REQUESTS", "50"))))
GITHUB_TREE_MAX_RESPONSE_BYTES = min(25_000_000, max(1_000_000, int(os.getenv("PATCHTRACE_GITHUB_TREE_MAX_RESPONSE_BYTES", "18000000"))))
GITHUB_SKIP_FILENAMES = {
    "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lock", "bun.lockb",
    "cargo.lock", "poetry.lock", "pipfile.lock", "composer.lock", "go.sum",
}
GITHUB_TEXT_SPECIAL_NAMES = {
    "license", "makefile", "dockerfile", "cmakelists.txt", "copying", "copying.lesser", "readme",
    "contributing", "code_of_conduct.md", "security.md", "support.md",
}


class GitHubSnapshotError(ValueError):
    """A safe, user-facing error while building a bounded public GitHub snapshot."""

    def __init__(self, message: str, *, retryable: bool = False, rate_limited: bool = False, not_found: bool = False):
        super().__init__(message)
        self.retryable = retryable
        self.rate_limited = rate_limited
        self.not_found = not_found


def _github_api_get_json(
    client: httpx.Client,
    url: str,
    *,
    budget: dict[str, int],
    max_response_bytes: int,
    params: dict[str, str] | None = None,
    not_found_message: str = "GitHub could not find this public repository resource.",
) -> Any:
    """Fetch one bounded GitHub API JSON response with explicit request and byte budgets."""
    if budget.get("remaining", 0) <= 0:
        raise GitHubSnapshotError(
            "GitHub API request limit reached while building a safe snapshot.",
            retryable=True,
        )
    budget["remaining"] -= 1
    budget["used"] = budget.get("used", 0) + 1

    try:
        with client.stream("GET", url, params=params) as response:
            status = int(response.status_code)
            headers = response.headers
            remaining = str(headers.get("X-RateLimit-Remaining", "")).strip()
            if status == 404:
                raise GitHubSnapshotError(not_found_message, not_found=True)
            if status in {403, 429}:
                limited = status == 429 or remaining == "0" or bool(headers.get("Retry-After"))
                if limited:
                    raise GitHubSnapshotError(
                        "GitHub API rate limit reached. Wait a little and retry the inspection.",
                        retryable=True,
                        rate_limited=True,
                    )
                raise GitHubSnapshotError(
                    "GitHub denied API access. The repository may be private or public access may be restricted.",
                    not_found=False,
                )
            if status == 401:
                raise GitHubSnapshotError("GitHub API authentication was rejected for this public request.")
            if status >= 500:
                raise GitHubSnapshotError(
                    f"GitHub API is temporarily unavailable (HTTP {status}).",
                    retryable=True,
                )
            if status >= 400:
                raise GitHubSnapshotError(f"GitHub rejected the API request (HTTP {status}).")

            content_length = headers.get("Content-Length")
            declared_size = -1
            if content_length:
                try:
                    declared_size = int(content_length)
                except (TypeError, ValueError):
                    declared_size = -1
            if declared_size > max_response_bytes:
                raise GitHubSnapshotError(
                    "GitHub returned a response larger than PatchTrace's configured safety limit."
                )

            chunks: list[bytes] = []
            received = 0
            for chunk in response.iter_bytes():
                if not chunk:
                    continue
                received += len(chunk)
                if received > max_response_bytes:
                    raise GitHubSnapshotError(
                        "GitHub returned a response larger than PatchTrace's configured safety limit."
                    )
                chunks.append(chunk)
            try:
                return json.loads(b"".join(chunks).decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise GitHubSnapshotError("GitHub returned malformed JSON; the repository was not treated as inspected.") from exc
    except httpx.TimeoutException as exc:
        raise GitHubSnapshotError(
            "Timed out while reading GitHub repository data. Try again or provide a direct issue URL.",
            retryable=True,
        ) from exc
    except httpx.RequestError as exc:
        raise GitHubSnapshotError(
            "Network error while reading GitHub repository data. Check connectivity and retry.",
            retryable=True,
        ) from exc


def _priority_text_tokens(priority_text: str) -> set[str]:
    return {
        token for token in re.findall(r"[a-z0-9_]+", (priority_text or "").lower())
        if len(token) >= 3 and token not in STOPWORDS
    }


def _path_relevance_boost(relative: str, priority_text: str) -> int:
    """Boost exact paths and meaningful filename references from an issue or user task."""
    if not priority_text:
        return 0
    path_lower = relative.lower().replace("\\", "/")
    text_lower = priority_text.lower().replace("\\", "/")
    score = 0
    if path_lower in text_lower:
        score += 1500
    basename = Path(relative).name.lower()
    if len(basename) >= 5 and re.search(r"(?<![a-z0-9])" + re.escape(basename) + r"(?![a-z0-9])", text_lower):
        score += 450
    tokens = set(re.findall(r"[a-z0-9_]+", path_lower))
    overlap = tokens.intersection(_priority_text_tokens(priority_text))
    score += min(180, len(overlap) * 18)
    return score


def _github_path_supported(relative: str) -> bool:
    normalized = relative.replace("\\", "/")
    if not normalized or normalized.startswith("/") or "\\" in relative:
        return False
    parts = normalized.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        return False
    lowered = [part.lower() for part in parts]
    skipped_dirs = {item.lower() for item in SKIP_DIRS}
    if any(part in skipped_dirs for part in lowered[:-1]):
        return False
    if any(part.startswith(".") and part != ".github" for part in lowered[:-1]):
        return False
    filename = lowered[-1]
    if filename in GITHUB_SKIP_FILENAMES or filename.endswith((".min.js", ".min.css", ".map")):
        return False
    return Path(filename).suffix.lower() in TEXT_EXTS or filename in GITHUB_TEXT_SPECIAL_NAMES


def _github_path_base_priority(relative: str) -> int:
    """Keep project guidance/configuration and tests without pushing source out of the snapshot."""
    path = Path(relative)
    low = relative.lower()
    name = path.name.lower()
    depth = len(path.parts)
    root_configs = {
        "pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml",
        "requirements.txt", "makefile", "cmakelists.txt", "cmakepresets.json",
        "tox.ini", "pytest.ini", "meson.build", "build.gradle", "build.gradle.kts",
        "settings.gradle", "settings.gradle.kts",
    }
    if depth == 1 and name.startswith("readme"):
        return 120
    if depth <= 2 and ("contributing" in name or name in {"development.md", "hacking.md", "developer-guide.md"}):
        return 115
    if depth == 1 and (name in root_configs or name.startswith("license")):
        return 100
    if low.startswith(".github/") and any(term in name for term in ("contribut", "workflow", "issue_template", "pull_request_template")):
        return 92
    if _is_test_surface(relative):
        return 82
    if depth <= 3 and Path(relative).suffix.lower() in {".md", ".rst", ".txt"}:
        return 45
    if Path(relative).suffix.lower() in {
        ".py", ".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".vue", ".svelte",
        ".go", ".rs", ".java", ".rb", ".sql", ".c", ".cc", ".cpp", ".cxx",
        ".h", ".hh", ".hpp", ".hxx", ".m", ".mm", ".swift", ".kt", ".kts",
        ".cs", ".php", ".dart", ".ex", ".exs", ".erl", ".hrl", ".hs", ".lhs",
        ".pl", ".pm", ".r", ".jl", ".lua", ".scala", ".clj", ".cljs", ".sol",
    }:
        return 72
    if Path(relative).suffix.lower() in {".toml", ".yaml", ".yml", ".json", ".xml", ".ini", ".cfg", ".gradle", ".properties", ".tf", ".hcl", ".proto"}:
        return 65
    if low.startswith(("docs/", "doc/", "documentation/")):
        return 48
    return 10


def _directory_priority(relative: str, priority_text: str) -> int:
    name = relative.lower()
    score = _path_relevance_boost(relative, priority_text)
    if name in {"src", "source", "sources", "app", "apps", "packages", "modules", "core", "lib", "libs", "include", "server", "client"}:
        score += 100
    elif name in {"tests", "test", "spec", "specs", "unit_test", "unit_tests"}:
        score += 95
    elif name in {"docs", "doc", "documentation"}:
        score += 70
    elif name == ".github":
        score += 55
    else:
        score += 5
    return score


def _decode_github_blob(blob: dict[str, Any]) -> str | None:
    """Decode a Git blob as text without accidentally treating binary data as source."""
    encoding = str(blob.get("encoding") or "").lower()
    content = blob.get("content")
    if not isinstance(content, str):
        return None
    try:
        if encoding == "base64":
            raw = base64.b64decode(content, validate=False)
        elif encoding in {"utf-8", "utf8"}:
            raw = content.encode("utf-8")
        else:
            return None
    except (ValueError, base64.binascii.Error):
        return None
    if b"\x00" in raw:
        return None
    try:
        decoded = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy source files are sometimes saved as Windows-1252. Decode common text
        # encodings gracefully, but still reject content that looks binary afterwards.
        try:
            decoded = raw.decode("utf-16")
        except UnicodeDecodeError:
            try:
                decoded = raw.decode("cp1252")
            except UnicodeDecodeError:
                return None
    if not decoded:
        return ""
    control_count = sum(1 for ch in decoded if ord(ch) < 32 and ch not in "\n\r\t\f")
    if control_count > max(2, len(decoded) // 100):
        return None
    return decoded


def _fetch_github_subtree_entries(
    client: httpx.Client,
    owner: str,
    repo: str,
    root_path: str,
    root_sha: str,
    priority_text: str,
    budget: dict[str, int],
    *,
    max_directories: int = 4,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Fetch a relevant subtree without letting one oversized directory starve the scan."""
    collected: list[dict[str, Any]] = []
    notes: list[str] = []
    pending: list[tuple[str, str, int]] = [(root_path.rstrip("/"), root_sha, 0)]
    seen: set[tuple[str, str]] = set()
    processed = 0
    max_depth = 4

    while pending and processed < max_directories and budget.get("remaining", 0) > 0:
        pending.sort(key=lambda item: (-_directory_priority(item[0], priority_text), item[0].lower()))
        relative_dir, tree_sha, depth = pending.pop(0)
        identity = (relative_dir, tree_sha)
        if identity in seen or depth > max_depth:
            continue
        seen.add(identity)
        processed += 1
        url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{quote(tree_sha, safe='')}"
        recursive = True
        try:
            payload = _github_api_get_json(
                client, url, budget=budget, params={"recursive": "1"},
                max_response_bytes=GITHUB_TREE_MAX_RESPONSE_BYTES,
                not_found_message=f"GitHub could not retrieve the {relative_dir} subtree.",
            )
            items = payload.get("tree", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                notes.append(f"GitHub returned an invalid tree for {relative_dir}")
                continue
            truncated = bool(payload.get("truncated")) if isinstance(payload, dict) else False
        except GitHubSnapshotError as exc:
            if "response larger than" not in str(exc).lower() or budget.get("remaining", 0) <= 0:
                notes.append(f"could not recursively inspect {relative_dir}: {str(exc)[:100]}")
                if budget.get("remaining", 0) <= 0:
                    break
                continue
            notes.append(f"the recursive tree for {relative_dir} exceeded the response-size limit")
            recursive = False
            try:
                payload = _github_api_get_json(
                    client, url, budget=budget,
                    max_response_bytes=GITHUB_TREE_MAX_RESPONSE_BYTES,
                    not_found_message=f"GitHub could not retrieve direct entries for {relative_dir}.",
                )
            except GitHubSnapshotError as shallow_error:
                notes.append(f"could not read direct entries for {relative_dir}: {str(shallow_error)[:90]}")
                if budget.get("remaining", 0) <= 0:
                    break
                continue
            items = payload.get("tree", []) if isinstance(payload, dict) else []
            if not isinstance(items, list):
                notes.append(f"GitHub returned an invalid direct tree for {relative_dir}")
                continue
            truncated = True

        child_directories: list[tuple[str, str, int]] = []
        for item in items:
            if not isinstance(item, dict) or not isinstance(item.get("path"), str):
                continue
            item_path = item["path"].strip("/")
            if not item_path or any(part in {"", ".", ".."} for part in item_path.split("/")):
                continue
            full_path = relative_dir + "/" + item_path
            if item.get("type") == "blob":
                expanded = dict(item)
                expanded["path"] = full_path
                collected.append(expanded)
            elif (
                item.get("type") == "tree"
                and isinstance(item.get("sha"), str)
                and depth < max_depth
                and _github_path_supported(full_path + "/placeholder.py")
            ):
                child_directories.append((full_path, item["sha"], depth + 1))

        # A complete recursive tree already includes all descendants. Descend only
        # after truncation or when we had to use the shallow, direct-child response.
        if truncated:
            child_directories.sort(
                key=lambda item: (-_directory_priority(item[0], priority_text), item[0].lower())
            )
            remaining_slots = max_directories - processed
            pending.extend(child_directories[:remaining_slots])
            if recursive:
                notes.append(f"GitHub truncated the {relative_dir} subtree")
    return collected, notes


def _fetch_github_api_snapshot(
    owner: str,
    repo: str,
    target: Path,
    priority_text: str = "",
) -> tuple[Path, str]:
    """Build a bounded source snapshot directly from GitHub REST when Git clone fails."""
    timeout = httpx.Timeout(
        connect=min(4.0, GITHUB_API_TIMEOUT_SECONDS),
        read=GITHUB_API_TIMEOUT_SECONDS,
        write=min(4.0, GITHUB_API_TIMEOUT_SECONDS),
        pool=min(4.0, GITHUB_API_TIMEOUT_SECONDS),
    )
    budget = {"remaining": GITHUB_API_MAX_REQUESTS, "used": 0}
    snapshot = target / "snapshot"
    shutil.rmtree(snapshot, ignore_errors=True)
    snapshot.mkdir(parents=True, exist_ok=True)
    saved_paths: list[str] = []
    saved_bytes = 0
    partial_reasons: list[str] = []
    last_file_error: str | None = None

    try:
        with httpx.Client(headers=GITHUB_API_HEADERS, timeout=timeout, follow_redirects=False) as client:
            # Resolve the real default branch and traverse the tree before fetching any blobs.
            metadata = _github_api_get_json(
                client,
                f"{GITHUB_API}/repos/{owner}/{repo}",
                budget=budget,
                max_response_bytes=500_000,
                not_found_message="GitHub repository was not found or is not publicly accessible.",
            )
            default_branch = metadata.get("default_branch") if isinstance(metadata, dict) else None
            if not isinstance(default_branch, str) or not default_branch.strip():
                raise GitHubSnapshotError("GitHub did not return a usable default branch for this repository.")
            encoded_branch = quote(default_branch, safe="")
            tree_url = f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{encoded_branch}"
            root_tree_already_fetched = False
            try:
                tree = _github_api_get_json(
                    client, tree_url, budget=budget, params={"recursive": "1"},
                    max_response_bytes=GITHUB_TREE_MAX_RESPONSE_BYTES,
                    not_found_message="GitHub could not retrieve the repository's default-branch tree.",
                )
                if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                    raise GitHubSnapshotError("GitHub returned an invalid repository tree.")
                entries = list(tree["tree"])
                tree_truncated = bool(tree.get("truncated"))
            except GitHubSnapshotError as exc:
                # Very large recursive responses can exceed the response cap before GitHub
                # can return its truncated flag. Fall back to the shallow root tree and
                # recursively inspect only high-value subtrees.
                if "response larger than" not in str(exc).lower() or budget.get("remaining", 0) <= 0:
                    raise
                tree = _github_api_get_json(
                    client, tree_url, budget=budget,
                    max_response_bytes=GITHUB_TREE_MAX_RESPONSE_BYTES,
                    not_found_message="GitHub could not retrieve the repository's root tree.",
                )
                if not isinstance(tree, dict) or not isinstance(tree.get("tree"), list):
                    raise GitHubSnapshotError("GitHub returned an invalid root tree after the recursive tree exceeded the response limit.")
                entries = list(tree["tree"])
                tree_truncated = True
                root_tree_already_fetched = True
                partial_reasons.append("the recursive tree exceeded the configured response-size limit")
            if tree_truncated:
                partial_reasons.append("GitHub marked the recursive tree as truncated")
                root_sha = tree.get("sha") or encoded_branch
                try:
                    if root_tree_already_fetched:
                        root_tree = tree
                    else:
                        root_tree = _github_api_get_json(
                            client,
                            f"{GITHUB_API}/repos/{owner}/{repo}/git/trees/{quote(str(root_sha), safe='')}",
                            budget=budget,
                            max_response_bytes=GITHUB_TREE_MAX_RESPONSE_BYTES,
                            not_found_message="GitHub could not retrieve the repository root tree.",
                        )
                    root_entries = root_tree.get("tree", []) if isinstance(root_tree, dict) else []
                    if isinstance(root_entries, list):
                        entries.extend(root_entries)
                        dirs = [
                            item for item in root_entries
                            if isinstance(item, dict) and item.get("type") == "tree"
                            and isinstance(item.get("path"), str) and isinstance(item.get("sha"), str)
                            and Path(item["path"]).name.lower() not in {name.lower() for name in SKIP_DIRS}
                        ]
                        dirs.sort(key=lambda item: (-_directory_priority(item["path"], priority_text), item["path"].lower()))
                        subtree_count = min(5, max(0, budget["remaining"] - 8))
                        for directory in dirs[:subtree_count]:
                            subtree_entries, subtree_notes = _fetch_github_subtree_entries(
                                client,
                                owner,
                                repo,
                                directory["path"],
                                directory["sha"],
                                priority_text,
                                budget,
                                max_directories=4,
                            )
                            entries.extend(subtree_entries)
                            partial_reasons.extend(subtree_notes)
                            if budget.get("remaining", 0) <= 0:
                                break

            unique: dict[str, dict[str, Any]] = {}
            for entry in entries:
                if not isinstance(entry, dict) or entry.get("type") != "blob":
                    continue
                path = entry.get("path")
                if not isinstance(path, str) or not _github_path_supported(path):
                    continue
                size_value = entry.get("size")
                try:
                    size = int(size_value) if size_value is not None else -1
                except (TypeError, ValueError):
                    size = -1
                if size > MAX_FILE_BYTES or size > MAX_TOTAL_BYTES:
                    continue
                existing = unique.get(path)
                if existing is None or (existing.get("size") is None and size_value is not None):
                    unique[path] = entry
            candidates = sorted(
                unique.values(),
                key=lambda item: (
                    -(_path_relevance_boost(str(item["path"]), priority_text) + _github_path_base_priority(str(item["path"]))),
                    len(Path(str(item["path"])).parts),
                    str(item["path"]).lower(),
                ),
            )
            if not candidates:
                raise GitHubSnapshotError("GitHub repository contains no supported text/code files under the current scan rules.")

            selected_limit = min(MAX_FILES, len(candidates))
            if selected_limit < len(candidates):
                partial_reasons.append(f"file-count limit ({MAX_FILES}) reached")
            blob_response_cap = min(3_000_000, max(96_000, int(MAX_FILE_BYTES * 1.6) + 65_536))
            # Continue down the ranked list when a blob is binary, oversized after
            # decoding, or unavailable, so one bad candidate does not starve a
            # useful source/test file. The request budget still bounds network work.
            for entry in candidates:
                if len(saved_paths) >= MAX_FILES:
                    partial_reasons.append(f"file-count limit ({MAX_FILES}) reached")
                    break
                path = str(entry["path"])
                try:
                    expected_size = int(entry.get("size")) if entry.get("size") is not None else -1
                except (TypeError, ValueError):
                    expected_size = -1
                if expected_size > MAX_FILE_BYTES:
                    continue
                if expected_size >= 0 and saved_bytes + expected_size > MAX_TOTAL_BYTES:
                    # A later, smaller but more relevant file may still fit.
                    continue
                if budget["remaining"] <= 0:
                    partial_reasons.append("GitHub API request budget reached")
                    break
                blob_sha = entry.get("sha")
                if not isinstance(blob_sha, str) or not blob_sha:
                    continue
                try:
                    blob = _github_api_get_json(
                        client,
                        f"{GITHUB_API}/repos/{owner}/{repo}/git/blobs/{quote(blob_sha, safe='')}",
                        budget=budget,
                        max_response_bytes=blob_response_cap,
                        not_found_message=f"GitHub no longer has the blob for {path}.",
                    )
                except GitHubSnapshotError as exc:
                    last_file_error = str(exc)
                    if exc.not_found:
                        continue
                    if (exc.retryable or exc.rate_limited) and saved_paths:
                        partial_reasons.append(str(exc))
                        break
                    if exc.retryable or exc.rate_limited:
                        raise
                    # One malformed or unavailable blob should not discard other source files.
                    continue
                if not isinstance(blob, dict):
                    last_file_error = f"GitHub returned an invalid blob for {path}."
                    continue
                content = _decode_github_blob(blob)
                if content is None:
                    continue
                encoded_size = len(content.encode("utf-8"))
                if encoded_size > MAX_FILE_BYTES or saved_bytes + encoded_size > MAX_TOTAL_BYTES:
                    continue
                target_file = snapshot / Path(path)
                try:
                    target_file.parent.mkdir(parents=True, exist_ok=True)
                    target_file.write_text(content, encoding="utf-8", newline="")
                except (OSError, ValueError):
                    continue
                saved_paths.append(path)
                saved_bytes += encoded_size

            if not saved_paths:
                detail = f" {last_file_error}" if last_file_error else ""
                raise GitHubSnapshotError(
                    "GitHub API fallback did not retrieve any supported source files. The repository was not treated as successfully inspected."
                    + detail
                )

            if budget["remaining"] <= 0:
                partial_reasons.append("GitHub API request budget reached")
            unique_reasons = list(dict.fromkeys(partial_reasons))
            note = f"GitHub API fallback snapshot of default branch {default_branch}; bounded sample"
            if unique_reasons:
                note += "; partial because " + "; ".join(unique_reasons[:3])
            return snapshot, note
    except GitHubSnapshotError:
        shutil.rmtree(target, ignore_errors=True)
        raise
    except Exception as exc:
        shutil.rmtree(target, ignore_errors=True)
        if isinstance(exc, (OSError, ValueError)):
            raise GitHubSnapshotError(f"Could not build a GitHub API snapshot: {str(exc)[:220]}") from exc
        raise GitHubSnapshotError("Could not build a GitHub API snapshot because GitHub returned an unexpected response.") from exc



def _is_test_surface(relative: str) -> bool:
    """Recognise common test directories and file names across Python, C++, and monorepos."""
    path = Path(relative)
    test_component = re.compile(r"(?:^|[_-])(?:unit[_-]?)?tests?(?:$|[_-])", flags=re.IGNORECASE)
    if any(test_component.search(part.lower()) for part in path.parts[:-1]):
        return True
    stem = path.stem.lower()
    return bool(test_component.search(stem))


def _copy_local_snapshot(source: Path, destination: Path, priority_text: str = "") -> None:
    """Copy a bounded, useful snapshot with project metadata and source before bulky docs."""
    destination.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[int, int, int, str, str, Path, int]] = []
    root_names = {
        "readme.md", "readme.rst", "contributing.md", "contributing.rst",
        "license", "license.md", "license.txt", "copying", "copying.lesser",
        "cmakelists.txt", "cmakepresets.json", "makefile", "dockerfile",
        "pyproject.toml", "package.json", "cargo.toml", "go.mod", "pom.xml",
        "requirements.txt", "tox.ini", "pytest.ini", "meson.build",
        "build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts",
    }
    source_dirs = {"src", "source", "sources", "modules", "include", "lib", "libs", "app", "apps", "core", "packages"}
    test_dirs = {"test", "tests", "spec", "specs", "unit_test", "unit_tests", "unittest", "unittests"}
    doc_dirs = {"doc", "docs", "documentation"}
    low_priority_dirs = {"examples", "extras", "demo", "demos", "benchmarks", "benchmark", "third_party", "third-party"}

    for current, dirs, names in os.walk(source):
        dirs[:] = sorted(
            d for d in dirs
            if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github")
        )
        rel_dir = Path(current).relative_to(source)
        for name in names:
            src = Path(current) / name
            if src.suffix.lower() not in TEXT_EXTS and name not in {"LICENSE", "Makefile", "Dockerfile", "CMakeLists.txt"}:
                continue
            try:
                if src.is_symlink() or not src.is_file():
                    continue
                size = src.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES:
                continue

            relative = (rel_dir / name).as_posix()
            parts = Path(relative).parts
            lower_parts = [part.lower() for part in parts]
            basename = name.lower()
            if len(parts) == 1 and basename in root_names:
                priority = 0
            elif relative.lower().startswith(".github/") and any(term in basename for term in ("contribut", "workflow", "issue_template")):
                priority = 1
            elif len(parts) == 1 and (basename.startswith("readme") or basename.startswith("license")):
                priority = 0
            elif any(part in test_dirs for part in lower_parts) or _is_test_surface(relative):
                priority = 3
            elif parts and lower_parts[0] in source_dirs:
                priority = 2
            elif any(part in doc_dirs for part in lower_parts):
                priority = 4
            elif any(part in low_priority_dirs for part in lower_parts):
                priority = 6
            elif basename in root_names or basename in {"makefile", "cmakelists.txt", "module.modulemap"}:
                priority = 3
            elif relative.lower().startswith(".github/workflows/"):
                priority = 4
            else:
                priority = 5

            candidates.append((priority, _path_relevance_boost(relative, priority_text), len(parts), relative.lower(), relative, src, size))

    # Deterministic ordering avoids alphabetical traversal accidentally scanning docs
    # and examples while omitting the project's actual source language.
    candidates.sort(key=lambda item: (-item[1], item[0], item[2], item[3]))
    total = 0
    count = 0
    for _, _, _, _, relative, src, size in candidates:
        if total + size > MAX_TOTAL_BYTES or count >= MAX_FILES:
            continue
        target = destination / Path(relative)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, target)
        except (OSError, shutil.Error):
            continue
        total += size
        count += 1


def prepare_repository(source_type: str, source: str, priority_text: str = "") -> tuple[str, Path, str]:
    """Prepare a bounded repository snapshot, using GitHub REST if a public clone fails."""
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    repo_id = uuid.uuid4().hex[:12]
    target = WORKSPACE / repo_id
    if source_type == "demo":
        _copy_local_snapshot(DEMO_REPO, target, priority_text)
        if not any(path.is_file() for path in target.rglob("*")):
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError("The bundled demo repository contains no supported text/code files.")
        return repo_id, target, "Synthetic demo repository (included with PatchTrace)"
    if source_type == "local":
        path = Path(source).expanduser().resolve()
        if not source or not path.is_dir():
            raise ValueError("Local source must be an existing repository directory on the computer running the backend.")
        _copy_local_snapshot(path, target, priority_text)
        if not any(item.is_file() for item in target.rglob("*")):
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError("No supported text/code files were found in that directory.")
        return repo_id, target, f"Local snapshot: {path.name}"
    if source_type == "github":
        github_input = parse_github_input(source)
        if not github_input:
            raise ValueError("Enter a public GitHub repository URL or issue URL such as https://github.com/OWNER/REPO/issues/123.")
        owner, repo = github_input["owner"], github_input["repo"]
        clone_url = f"https://github.com/{owner}/{repo}.git"
        target.mkdir(parents=True, exist_ok=True)
        clone_dir = target / "checkout"
        try:
            clone_timeout = min(120.0, max(1.0, float(os.getenv("PATCHTRACE_GIT_CLONE_TIMEOUT_SECONDS", "90"))))
        except ValueError:
            clone_timeout = 90.0
        clone_error: str | None = None
        try:
            proc = subprocess.run(
                [
                    "git", "-c", "core.hooksPath=/dev/null", "clone",
                    "--depth", "1", "--no-tags", "--no-recurse-submodules",
                    clone_url, str(clone_dir),
                ],
                capture_output=True,
                text=True,
                timeout=clone_timeout,
                check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
            )
            if proc.returncode != 0:
                clone_error = f"Git clone failed (exit code {proc.returncode})"
            else:
                snapshot = target / "snapshot"
                _copy_local_snapshot(clone_dir, snapshot, priority_text)
                if any(item.is_file() for item in snapshot.rglob("*")):
                    shutil.rmtree(clone_dir, ignore_errors=True)
                    return repo_id, snapshot, f"Public GitHub repository: {owner}/{repo}"
                clone_error = "Git clone completed but no supported text/code files were found"
        except subprocess.TimeoutExpired:
            clone_error = f"Git clone timed out after {clone_timeout:g} seconds"
        except FileNotFoundError:
            clone_error = "Git is not installed or could not be started"
        except OSError as exc:
            clone_error = f"Git clone could not start ({type(exc).__name__})"
        finally:
            # A partial checkout must never be treated as a repository snapshot.
            shutil.rmtree(clone_dir, ignore_errors=True)

        try:
            snapshot, fallback_note = _fetch_github_api_snapshot(
                owner, repo, target, priority_text or source
            )
            if not any(item.is_file() for item in snapshot.rglob("*")):
                raise GitHubSnapshotError(
                    "GitHub API fallback returned no usable files. The repository was not treated as inspected."
                )
            return repo_id, snapshot, f"Public GitHub repository: {owner}/{repo} ({fallback_note})"
        except GitHubSnapshotError as api_error:
            shutil.rmtree(target, ignore_errors=True)
            if clone_error:
                raise ValueError(
                    f"{clone_error}. GitHub API fallback failed: {str(api_error)[:260]}"
                ) from api_error
            raise ValueError(f"GitHub API fallback failed: {str(api_error)[:260]}") from api_error
    raise ValueError("Unsupported source type.")


def _read_text(path: Path, limit: int = 30000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _line_evidence(path: Path, relative: str, task_tokens: set[str], task: str = "") -> list[dict[str, Any]]:
    """Select relevant line receipts, prioritising README command and failure context."""
    evidence: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return evidence
    if not lines:
        return evidence

    task_low = task.lower()
    is_readme = Path(relative).name.lower().startswith("readme")
    command_task = any(word in task_low for word in (
        "quick start", "quick-start", "command", "script", "missing", "entry point",
        "documentation", "document", "setup", "install", "launch", "run",
    ))

    quick_start_range: tuple[int, int] | None = None
    if is_readme and command_task:
        for idx, line in enumerate(lines):
            if re.search(r"(?i)^\s*#{1,6}\s+quick[ -]?start\b", line):
                end_idx = len(lines)
                for later in range(idx + 1, len(lines)):
                    if re.match(r"^\s*#{1,6}\s+", lines[later]):
                        end_idx = later
                        break
                quick_start_range = (idx, end_idx)
                break

    command_intro_indices: set[int] = set()
    if is_readme and command_task:
        for idx, line in enumerate(lines):
            if re.search(r"(?i)\b(run|execute|launch|start)\b.*\b(cli|help|command|application|program)\b", line):
                for later in range(idx + 1, min(idx + 7, len(lines))):
                    if lines[later].strip().startswith(chr(96) * 3):
                        for cmd_idx in range(later + 1, min(later + 8, len(lines))):
                            candidate = lines[cmd_idx].strip()
                            if candidate.startswith(chr(96) * 3):
                                break
                            if candidate:
                                command_intro_indices.add(cmd_idx)
                        break

    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines, 1):
        lower = line.lower()
        score = sum(
            1 for token in task_tokens
            if re.search(r"(?<![a-z0-9_])" + re.escape(token) + r"(?![a-z0-9_])", lower)
        )
        in_quick_start = bool(quick_start_range and quick_start_range[0] < idx - 1 < quick_start_range[1])
        if is_readme and command_task:
            if in_quick_start and re.search(r"(?i)\b(python|python3|npm|npx|pnpm|yarn|pip|uv|pytest|cargo|go)\b", lower):
                score += 4
            if idx - 1 in command_intro_indices:
                score += 16
            if re.search(r"(?i)\b(intentionally stale|does not exist|no [\x60']?app\.py|missing script|command above|entry point lives)\b", line):
                score += 14
            if re.search(r"(?i)^\s*#{1,6}\s+quick[ -]?start\b", line):
                score += 5
            if re.search(r"(?i)\b(does not exist|not found|missing|stale|entry point)\b", line):
                score += 5
        if score:
            scored.append((score, idx, line.strip()[:260] or "(blank line)"))

    chosen = sorted(scored, key=lambda item: (-item[0], item[1]))[:3]
    for score, idx, line in chosen:
        line_index = idx - 1
        context_start = max(0, line_index - 2)
        context_end = min(len(lines), line_index + 3)
        context = "\n".join(
            f"{number + 1}: {lines[number][:220]}"
            for number in range(context_start, context_end)
        )
        evidence.append({
            "path": relative,
            "line": idx,
            "text": line,
            "context": context,
            "relevance": min(score, 10),
        })
    # No arbitrary file headers: an empty evidence list is better than a misleading citation.
    return evidence




def _is_exploratory_task(task: str) -> bool:
    low = task.lower()
    return any(phrase in low for phrase in (
        "contribution opportunity",
        "contribution opportunities",
        "explore this repository",
        "exploratory repository review",
        "most promising contribution",
        "find a contribution",
        "discover a contribution",
    ))


_DISCOVERY_SIGNAL_PATTERNS: tuple[tuple[str, int], ...] = (
    (r"\bFIXME\b", 12),
    (r"\bBUG\s*:", 11),
    (r"\bnot implemented\b", 11),
    (r"\bintentionally stale\b", 11),
    (r"\bdoes not exist\b", 10),
    (r"\bknown issue\b", 9),
    (r"\bdeprecated\b", 8),
    (r"\bobsolete\b", 8),
    (r"\bXXX\b", 7),
    (r"\bworkaround\b", 7),
    (r"\bTODO\b", 6),
    (r"\bHACK\b", 5),
)


def _discovery_evidence(root: Path, relatives: list[str]) -> list[dict[str, Any]]:
    """Return current, line-level signals worth investigating, not claims of confirmed bugs."""
    code_exts = {
        ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".sql",
        ".swift", ".kt", ".kts", ".cs",
    }
    found: list[tuple[int, str, int, str, str]] = []
    for relative in relatives:
        path = Path(relative)
        low_path = relative.lower()
        basename = path.name.lower()
        if basename.startswith("license") or basename in {"copying", "copying.lesser"}:
            continue
        # Historical release notes contain old issue descriptions, not necessarily current opportunities.
        if any(term in basename for term in ("breaking_changes", "changelog", "release_notes", "history")):
            continue
        full_path = root / relative
        try:
            lines = full_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        is_source = path.suffix.lower() in code_exts
        is_documentation = path.suffix.lower() in {".md", ".rst", ".txt"}
        for line_number, line in enumerate(lines, 1):
            stripped_line = line.strip()
            if not stripped_line or len(stripped_line) > 320:
                continue
            # Preprocessor errors often deliberately guard unsupported platforms/features;
            # normal runtime messages like "client does not exist" are not TODOs or bugs.
            if is_source and re.match(r"^#\s*error\b", stripped_line):
                continue
            if is_source:
                allowed_source_markers = ("FIXME", "BUG", "TODO", "XXX", "HACK", "WORKAROUND")
                allowed_source_patterns = [
                    (pattern, weight) for pattern, weight in _DISCOVERY_SIGNAL_PATTERNS
                    if any(marker in pattern.upper() for marker in allowed_source_markers)
                ]
                hits = [weight for pattern, weight in allowed_source_patterns if re.search(pattern, line, flags=re.IGNORECASE)]
            elif is_documentation:
                hits = [weight for pattern, weight in _DISCOVERY_SIGNAL_PATTERNS if re.search(pattern, line, flags=re.IGNORECASE)]
            else:
                hits = [weight for pattern, weight in _DISCOVERY_SIGNAL_PATTERNS if re.search(pattern, line, flags=re.IGNORECASE)]
            if not hits:
                continue
            score = max(hits)
            if is_source:
                score += 3
            if any(part.lower() in {"tests", "test", "spec", "specs"} for part in path.parts):
                score += 1
            if low_path.startswith(("docs/", "doc/")):
                score -= 1
            context_start = max(0, line_number - 3)
            context_end = min(len(lines), line_number + 2)
            context = "\n".join(
                f"{number + 1}: {lines[number][:220]}"
                for number in range(context_start, context_end)
            )
            found.append((score, relative, line_number, stripped_line[:260], context))

    # Diversify the shortlist: one strongest finding per file first, then a second only if needed.
    found.sort(key=lambda item: (-item[0], item[1].lower(), item[2]))
    selected: list[dict[str, Any]] = []
    per_file: dict[str, int] = {}
    seen_lines: set[tuple[str, int]] = set()
    for score, relative, line_number, text, context in found:
        key = (relative, line_number)
        if key in seen_lines or per_file.get(relative, 0) >= 2:
            continue
        seen_lines.add(key)
        per_file[relative] = per_file.get(relative, 0) + 1
        selected.append({
            "path": relative,
            "line": line_number,
            "text": text,
            "context": context,
            "relevance": min(score, 10),
        })
        if len(selected) >= 12:
            break
    return selected

def inspect_repository(root: Path, repo_id: str, source_label: str, task: str) -> dict[str, Any]:
    files: list[Path] = []
    byte_total = 0
    for current, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github"))
        for name in sorted(names):
            path = Path(current) / name
            if path.suffix.lower() not in TEXT_EXTS and name not in {"LICENSE", "Makefile", "Dockerfile"}:
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > MAX_FILE_BYTES or byte_total + size > MAX_TOTAL_BYTES:
                continue
            files.append(path)
            byte_total += size
            if len(files) >= MAX_FILES:
                break
        if len(files) >= MAX_FILES:
            break

    relatives = [str(p.relative_to(root)).replace(os.sep, "/") for p in files]
    languages: Counter[str] = Counter()
    for rel in relatives:
        ext = Path(rel).suffix.lower()
        lang = {
            ".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript",
            ".ts": "TypeScript", ".tsx": "TypeScript", ".md": "Markdown",
            ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML",
            ".css": "CSS", ".html": "HTML", ".go": "Go", ".rs": "Rust",
            ".java": "Java", ".rb": "Ruby", ".sql": "SQL", ".sh": "Shell",
            ".c": "C", ".cc": "C++", ".cpp": "C++", ".cxx": "C++",
            ".h": "C/C++ header", ".hh": "C++", ".hpp": "C++", ".hxx": "C++",
            ".m": "Objective-C", ".mm": "Objective-C++", ".cmake": "CMake",
            ".gradle": "Gradle", ".xml": "XML", ".swift": "Swift",
            ".kt": "Kotlin", ".kts": "Kotlin", ".cs": "C#",
        }.get(ext)
        if lang:
            languages[lang] += 1
    manifest_paths = [r for r in relatives if Path(r).name in {
        "pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml",
        "requirements.txt", "Makefile", "tox.ini", "pytest.ini", "CMakeLists.txt",
        "CMakePresets.json", "meson.build", "build.gradle", "build.gradle.kts",
        "settings.gradle", "settings.gradle.kts",
    }]
    manifest_paths.sort(key=lambda r: (len(Path(r).parts) != 1, len(Path(r).parts), r.lower()))
    readme = next((root / r for r in relatives if Path(r).name.lower() == "readme.md"), None)
    readme_text = _read_text(readme) if readme else ""
    readmes = sorted(
        [r for r in relatives if Path(r).name.lower().startswith("readme")],
        key=lambda r: (len(Path(r).parts) != 1, len(Path(r).parts), r.lower()),
    )
    license_files = [r for r in relatives if Path(r).name.upper().startswith("LICENSE") or Path(r).name.upper() in {"COPYING", "COPYING.LESSER"}]
    license_files.sort(key=lambda r: (len(Path(r).parts) != 1, len(Path(r).parts), r.lower()))
    ci_files = [r for r in relatives if ".github/workflows/" in r or r.startswith(".github/workflows/")]
    test_dirs = sorted({
        str(Path(r).parent)
        for r in relatives
        if _is_test_surface(r)
    })
    entry_names = {
        "main.py", "app.py", "cli.py", "index.ts", "index.js", "main.ts", "main.js", "__main__.py",
        "main.c", "main.cc", "main.cpp", "main.cxx", "main.m", "main.mm", "main.rs", "main.go",
    }
    entry_points = [
        r for r in relatives
        if Path(r).name.lower() in entry_names
        and not (Path(r).name.lower() in {"index.ts", "index.js"} and len(Path(r).parts) > 4)
    ]
    entry_points.sort(key=lambda r: (
        len(Path(r).parts),
        0 if Path(r).parts[0].lower() in {"src", "source", "app", "apps", "lib", "include"} else 1,
        r.lower(),
    ))
    entry_points = entry_points[:8]
    contributor_instructions = []
    for r in relatives:
        low = r.lower()
        if any(term in low for term in ("contributing", "developer-guide", "development", "hacking")):
            contributor_instructions.append(r)
    if readme_text:
        for line in readme_text.splitlines():
            low = line.lower()
            if any(term in low for term in ("contribut", "development setup", "run tests", "pytest", "npm test", "make test")):
                contributor_instructions.append(f"README.md: {line.strip()[:180]}")
    contributor_instructions = contributor_instructions[:10]

    exploratory = _is_exploratory_task(task)
    tokens = {t for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", task.lower()) if t not in STOPWORDS}
    ranked: list[dict[str, Any]] = []
    code_exts = {
        ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm",
        ".py", ".js", ".jsx", ".ts", ".tsx", ".go", ".rs", ".java", ".rb", ".sql",
        ".swift", ".kt", ".kts", ".cs",
    }
    root_configs = {
        "cmakelists.txt", "makefile", "meson.build", "pyproject.toml", "package.json",
        "cargo.toml", "go.mod", "pom.xml", "requirements.txt", "build.gradle",
        "build.gradle.kts", "settings.gradle", "settings.gradle.kts", "cmakepresets.json",
    }
    for path, rel in zip(files, relatives):
        file_text = _read_text(path, 12000)
        low = file_text.lower()
        name_low = Path(rel).name.lower()
        parts = Path(rel).parts
        if exploratory:
            if name_low.startswith("license") or name_low in {"copying", "copying.lesser"}:
                continue
            if any(term in name_low for term in ("breaking_changes", "changelog", "release_notes", "history")):
                continue
            score = 0
            reason = "Repository orientation file; inspect before proposing a contribution."
            if len(parts) == 1 and name_low.startswith("readme"):
                score += 42
                reason = "Root README; starting point for contributor setup and project intent."
            elif len(parts) == 1 and ("contribut" in name_low or "developer" in name_low or "hacking" in name_low):
                score += 40
                reason = "Contributor/developer guidance; explains project conventions."
            elif rel.lower().startswith(".github/") and ("contribut" in name_low or "issue_template" in name_low or "pull_request_template" in name_low):
                score += 34
                reason = "Maintainer contribution guidance or issue template."
            elif len(parts) == 1 and name_low in root_configs:
                score += 30
                reason = "Root build/package configuration; useful for verifying documented commands."
            elif _is_test_surface(rel):
                score += 16
                reason = "Test file or test surface; useful for finding uncovered or unverified behaviour."
            elif Path(rel).suffix.lower() in code_exts:
                score += 12
                reason = "Project source/header file; candidate for inspection, not a confirmed defect."
            elif len(parts) <= 2 and Path(rel).suffix.lower() in {".md", ".rst", ".txt"}:
                score += 8
                reason = "High-level project documentation."
            elif rel.lower().startswith(".github/workflows/"):
                score += 10
                reason = "CI workflow; useful for checking existing verification coverage."
            if score == 0:
                continue
            signal_count = sum(
                len(re.findall(pattern, file_text, flags=re.IGNORECASE))
                for pattern, _weight in _DISCOVERY_SIGNAL_PATTERNS
            )
            score += min(signal_count, 4) * 4
            ranked.append({"path": rel, "score": score, "reason": reason})
        else:
            token_hits = sum(
                1 for token in tokens
                if re.search(r"(?<![a-z0-9_])" + re.escape(token) + r"(?![a-z0-9_])", low)
            )
            name_hits = sum(
                2 for token in tokens
                if re.search(r"(?<![a-z0-9_])" + re.escape(token) + r"(?![a-z0-9_])", name_low)
            )
            extra = 0
            task_low = task.lower()
            if "readme" in task_low and name_low.startswith("readme"):
                extra += 5
            if any(word in task_low for word in ("test", "verify", "check")) and ("test" in rel.lower() or Path(rel).name in {"pyproject.toml", "package.json", "Makefile", "CMakeLists.txt"}):
                extra += 2
            if any(word in task_low for word in ("command", "quick-start", "documentation", "docs")) and Path(rel).suffix.lower() in {".md", ".rst", ".txt"}:
                extra += 2
            score = token_hits + name_hits + extra
            if score > 0 or name_low.startswith("readme"):
                ranked.append({"path": rel, "score": score, "reason": "Task keyword overlap + repository role; inspect the cited lines before editing."})
    ranked.sort(key=lambda item: (-item["score"], len(Path(item["path"]).parts), item["path"].lower()))
    candidates = ranked[:8]

    if exploratory:
        evidence = _discovery_evidence(root, relatives)
        # For follow-up evidence lookup, use signal tokens instead of generic prompt words.
        tokens = {"todo", "fixme", "bug", "deprecated", "obsolete", "xxx", "hack", "workaround", "stale", "implemented", "exists"}
    else:
        evidence = []
        for item in candidates[:6]:
            path = root / item["path"]
            evidence.extend(_line_evidence(path, item["path"], tokens, task)[:2])
        evidence = evidence[:12]

    checks: list[dict[str, Any]] = [{
        "id": "inventory", "label": "Repository inventory",
        "command": "Static file scan (no code execution)", "type": "static",
        "status": "passed",
        "detail": f"Scanned {len(relatives)} supported text/code files ({byte_total:,} bytes within size limits).",
    }]
    py_files = [r for r in relatives if Path(r).suffix.lower() == ".py"]
    test_files = [r for r in relatives if _is_test_surface(r)]
    python_test_files = [r for r in test_files if Path(r).suffix.lower() == ".py"]
    manifest_names = {Path(r).name.lower() for r in manifest_paths}
    if py_files:
        checks.append({
            "id": "python_compile", "label": "Python syntax compilation",
            "command": "python -m compileall -q .", "type": "safe-compile",
            "status": "not_run",
            "detail": "Suggested check only; it has not run. It compiles Python source without importing the project.",
        })
    if python_test_files:
        checks.append({
            "id": "pytest", "label": "Python regression tests",
            "command": "python -m pytest -q", "type": "exec-tests",
            "status": "not_run",
            "detail": "Suggested command only; tests have not run. Use a disposable environment before executing untrusted repository code.",
        })

    # Recommend commands only when a project manifest provides evidence for the tool or
    # script. These checks are informational and are never executed automatically.
    package_files = [r for r in manifest_paths if Path(r).name.lower() == "package.json"]
    for package_path in package_files[:3]:
        package_data = None
        try:
            package_data = json.loads(_read_text(root / package_path, 40_000))
        except (json.JSONDecodeError, OSError):
            package_data = None
        scripts = package_data.get("scripts", {}) if isinstance(package_data, dict) else {}
        if isinstance(scripts, dict) and isinstance(scripts.get("test"), str) and scripts["test"].strip():
            parent = Path(package_path).parent.as_posix()
            command = "npm test" if parent == "." else f"npm --prefix {parent} test"
            checks.append({
                "id": f"node_test_{len(checks)}", "label": "JavaScript/TypeScript test command",
                "command": command, "type": "suggested-command", "status": "not_run",
                "detail": f"The package manifest at {package_path} declares a test script. This command is a recommendation and has not run.",
            })

    if "go.mod" in manifest_names:
        checks.append({
            "id": "go_test", "label": "Go test command", "command": "go test ./...",
            "type": "suggested-command", "status": "not_run",
            "detail": "The repository includes go.mod. Suggested command only; not executed by PatchTrace.",
        })
    if "cargo.toml" in manifest_names:
        checks.append({
            "id": "rust_test", "label": "Rust test command", "command": "cargo test",
            "type": "suggested-command", "status": "not_run",
            "detail": "The repository includes Cargo.toml. Suggested command only; not executed by PatchTrace.",
        })
    if "pom.xml" in manifest_names:
        checks.append({
            "id": "maven_test", "label": "Java test command", "command": "mvn test",
            "type": "suggested-command", "status": "not_run",
            "detail": "The repository includes pom.xml. Suggested command only; not executed by PatchTrace.",
        })
    if "cmakelists.txt" in manifest_names and test_files:
        checks.append({
            "id": "cmake_test", "label": "C/C++ build and test command",
            "command": "cmake -S . -B build && cmake --build build && ctest --test-dir build --output-on-failure",
            "type": "suggested-command", "status": "not_run",
            "detail": "The repository includes CMake configuration and a test surface. Confirm its build instructions before running this suggested command.",
        })
    if any(Path(r).suffix.lower() in {".csproj", ".sln"} for r in relatives):
        checks.append({
            "id": "dotnet_test", "label": ".NET test command", "command": "dotnet test",
            "type": "suggested-command", "status": "not_run",
            "detail": "A .NET project file was detected. Suggested command only; not executed by PatchTrace.",
        })
    checks.append({
        "id": "model", "label": "AI-assisted task analysis",
        "command": "Gemma API (hosted) or explicitly configured Ollama", "type": "model",
        "status": "not_run",
        "detail": "A usable AI response is advisory and does not verify a proposed fix.",
    })

    overview = {
        "repo_id": repo_id,
        "source_label": source_label,
        "file_count": len(relatives),
        "bytes_scanned": byte_total,
        "truncated": len(relatives) >= MAX_FILES or byte_total >= MAX_TOTAL_BYTES or "bounded sample" in source_label.lower(),
        "languages": [{"name": k, "count": v} for k, v in languages.most_common(6)],
        "manifest_files": manifest_paths[:6],
        "readme_files": readmes[:8],
        "license_files": license_files[:3],
        "entry_points": entry_points,
        "test_directories": test_dirs[:8],
        "contributor_instructions": contributor_instructions[:5],
        "ci_files": ci_files[:5],
        "directories": sorted(
            {str(Path(r).parent) if str(Path(r).parent) != "." else "." for r in relatives},
            key=lambda d: (
                0 if d == "." else 1,
                {"src": 0, "source": 0, "modules": 0, "include": 1, "lib": 1, "app": 1, "apps": 1,
                 "tests": 2, "test": 2, "docs": 3, "doc": 3, "examples": 4, "extras": 4, ".github": 5}.get(Path(d).parts[0].lower() if d != "." else ".", 6),
                len(Path(d).parts),
                d.lower(),
            ),
        )[:16],
        "all_paths": relatives,
    }
    return {"repo_id": repo_id, "overview": overview, "candidates": candidates, "evidence": evidence, "checks": checks, "readme_text": readme_text[:20000], "task_tokens": sorted(tokens)[:40]}
