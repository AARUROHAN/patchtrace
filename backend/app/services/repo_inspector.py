from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import uuid
from collections import Counter
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

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
TEXT_EXTS = {".py", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json", ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh", ".ini", ".cfg", ".go", ".rs", ".java", ".rb", ".sql"}
STOPWORDS = {"this", "that", "with", "from", "have", "what", "when", "where", "which", "would", "could", "should", "about", "into", "file", "files", "issue", "task", "please", "help", "make", "need", "does", "not", "and", "the", "for", "are", "was", "were", "you", "your", "our", "can", "how", "why", "has", "its", "readme", "repo", "repository"}


def _copy_local_snapshot(source: Path, destination: Path) -> None:
    total = 0
    count = 0
    destination.mkdir(parents=True, exist_ok=True)
    for current, dirs, files in os.walk(source):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and (not d.startswith(".") or d == ".github"))
        rel = Path(current).relative_to(source)
        (destination / rel).mkdir(parents=True, exist_ok=True)
        for name in sorted(files):
            src = Path(current) / name
            if src.suffix.lower() not in TEXT_EXTS and name not in {"LICENSE", "Makefile", "Dockerfile"}:
                continue
            try:
                size = src.stat().st_size
                if size > MAX_FILE_BYTES or total + size > MAX_TOTAL_BYTES or count >= MAX_FILES:
                    continue
                target = destination / rel / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(src, target)
                total += size
                count += 1
            except (OSError, shutil.Error):
                continue


def prepare_repository(source_type: str, source: str) -> tuple[str, Path, str]:
    WORKSPACE.mkdir(parents=True, exist_ok=True)
    repo_id = uuid.uuid4().hex[:12]
    target = WORKSPACE / repo_id
    if source_type == "demo":
        _copy_local_snapshot(DEMO_REPO, target)
        return repo_id, target, "Synthetic demo repository (included with PatchTrace)"
    if source_type == "local":
        path = Path(source).expanduser().resolve()
        if not source or not path.is_dir():
            raise ValueError("Local source must be an existing repository directory on the computer running the backend.")
        _copy_local_snapshot(path, target)
        if not any(target.iterdir()):
            raise ValueError("No supported text/code files were found in that directory.")
        return repo_id, target, f"Local snapshot: {path.name}"
    if source_type == "github":
        parsed = urlparse(source)
        if parsed.scheme != "https" or parsed.netloc.lower() != "github.com":
            raise ValueError("For safety, public repository URLs must use https://github.com/OWNER/REPO.")
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        if len(parts) != 2:
            # Keep the accepted form deliberately narrow; branch subpaths are not cloned as separate repos.
            raise ValueError("Enter a repository root URL such as https://github.com/OWNER/REPO.")
        owner, repo = parts[:2]
        repo = repo.removesuffix(".git")
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repo):
            raise ValueError("The GitHub repository URL contains unsupported characters.")
        url = f"https://github.com/{owner}/{repo}.git"
        target.mkdir(parents=True, exist_ok=True)
        clone_dir = target / "checkout"
        try:
            proc = subprocess.run(
                ["git", "-c", "core.hooksPath=/dev/null", "clone", "--depth", "1", "--no-tags", "--no-recurse-submodules", url, str(clone_dir)],
                capture_output=True, text=True, timeout=90, check=False,
                env={**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1"},
            )
        except FileNotFoundError as exc:
            raise ValueError("Git is not installed. Install Git or use the demo repository.") from exc
        except subprocess.TimeoutExpired as exc:
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError("Repository clone timed out after 90 seconds.") from exc
        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "Git clone failed")[-800:]
            shutil.rmtree(target, ignore_errors=True)
            raise ValueError(f"Could not clone repository: {detail}")
        # Copy only bounded text files into the inspection snapshot, then remove the checkout.
        snapshot = target / "snapshot"
        _copy_local_snapshot(clone_dir, snapshot)
        shutil.rmtree(clone_dir, ignore_errors=True)
        if not any(snapshot.iterdir()):
            raise ValueError("The repository did not contain supported text/code files.")
        return repo_id, snapshot, f"Public GitHub repository: {owner}/{repo}"
    raise ValueError("Unsupported source type.")


def _read_text(path: Path, limit: int = 30000) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")[:limit]
    except OSError:
        return ""


def _line_evidence(path: Path, relative: str, task_tokens: set[str]) -> list[dict[str, Any]]:
    evidence: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return evidence
    scored: list[tuple[int, int, str]] = []
    for idx, line in enumerate(lines, 1):
        lower = line.lower()
        score = sum(1 for token in task_tokens if token in lower)
        if relative.lower().endswith("readme.md") and any(k in lower for k in ("quick start", "quick-start", "install", "test", "python ", "npm ", "pytest", "getting started")):
            score += 2
        if score:
            scored.append((score, idx, line.strip()[:260]))
    for score, idx, line in sorted(scored, key=lambda item: (-item[0], item[1]))[:3]:
        evidence.append({"path": relative, "line": idx, "text": line or "(blank line)", "relevance": min(score, 10)})
    if not evidence and lines:
        for idx, line in enumerate(lines[:2], 1):
            evidence.append({"path": relative, "line": idx, "text": line.strip()[:260] or "(blank line)", "relevance": 0})
    return evidence


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
        lang = {".py": "Python", ".js": "JavaScript", ".jsx": "JavaScript", ".ts": "TypeScript", ".tsx": "TypeScript", ".md": "Markdown", ".json": "JSON", ".yml": "YAML", ".yaml": "YAML", ".toml": "TOML", ".css": "CSS", ".html": "HTML", ".go": "Go", ".rs": "Rust", ".java": "Java", ".rb": "Ruby", ".sql": "SQL", ".sh": "Shell"}.get(ext)
        if lang:
            languages[lang] += 1
    manifest_paths = [r for r in relatives if Path(r).name in {"pyproject.toml", "package.json", "Cargo.toml", "go.mod", "pom.xml", "requirements.txt", "Makefile", "tox.ini", "pytest.ini"}]
    readme = next((root / r for r in relatives if Path(r).name.lower() == "readme.md"), None)
    readme_text = _read_text(readme) if readme else ""
    readmes = [r for r in relatives if Path(r).name.lower().startswith("readme")]
    license_files = [r for r in relatives if Path(r).name.upper().startswith("LICENSE") or Path(r).name.upper() in {"COPYING", "COPYING.LESSER"}]
    ci_files = [r for r in relatives if ".github/workflows/" in r or r.startswith(".github/workflows/")]
    test_dirs = sorted({str(Path(r).parent) for r in relatives if any(part.lower() in {"test", "tests", "spec", "specs"} for part in Path(r).parts)})
    entry_points = [r for r in relatives if Path(r).name in {"main.py", "app.py", "cli.py", "index.ts", "index.js", "main.ts", "main.js", "__main__.py"}][:12]
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

    tokens = {t for t in re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{2,}", task.lower()) if t not in STOPWORDS}
    ranked: list[dict[str, Any]] = []
    for path, rel in zip(files, relatives):
        text = _read_text(path, 12000)
        low = text.lower()
        token_hits = sum(1 for token in tokens if token in low)
        name_hits = sum(2 for token in tokens if token in Path(rel).name.lower())
        extra = 0
        task_low = task.lower()
        if "readme" in task_low and Path(rel).name.lower().startswith("readme"):
            extra += 5
        if any(word in task_low for word in ("test", "verify", "check")) and ("test" in rel.lower() or Path(rel).name in {"pyproject.toml", "package.json", "Makefile"}):
            extra += 2
        if any(word in task_low for word in ("command", "quick-start", "documentation", "docs")) and Path(rel).suffix.lower() in {".md", ".rst", ".txt"}:
            extra += 2
        score = token_hits + name_hits + extra
        if score > 0 or Path(rel).name.lower().startswith("readme"):
            ranked.append({"path": rel, "score": score, "reason": "Task keyword overlap + repository role; inspect the cited lines before editing."})
    ranked.sort(key=lambda x: (-x["score"], x["path"]))
    candidates = ranked[:8]
    evidence: list[dict[str, Any]] = []
    for item in candidates[:6]:
        path = root / item["path"]
        evidence.extend(_line_evidence(path, item["path"], tokens)[:2])
    evidence = evidence[:12]

    checks: list[dict[str, Any]] = [{"id": "inventory", "label": "Repository inventory", "command": "Static file scan (no code execution)", "type": "static", "status": "passed", "detail": f"Scanned {len(relatives)} supported text/code files ({byte_total:,} bytes within size limits)."}]
    py_files = [r for r in relatives if r.endswith(".py")]
    test_files = [r for r in relatives if Path(r).name.startswith("test_") or "/tests/" in f"/{r}/" or "/tests/" in r or "/test/" in r]
    if py_files:
        checks.append({"id": "python_compile", "label": "Python syntax compilation", "command": "python -m compileall -q .", "type": "safe-compile", "status": "not_run", "detail": "Available: compiles Python sources without importing the project. Run explicitly to record a real result."})
    if test_files:
        checks.append({"id": "pytest", "label": "Python test suite", "command": "python -m pytest -q", "type": "exec-tests", "status": "not_run", "detail": "Available: executes repository tests. Only run for a repository you trust, after confirming the warning."})
    checks.append({"id": "model", "label": "AI-assisted task analysis", "command": "Gemma API (hosted) or explicitly configured Ollama", "type": "model", "status": "not_run", "detail": "Marked passed only when the configured model returns a usable response; this does not verify a proposed fix."})

    overview = {
        "repo_id": repo_id,
        "source_label": source_label,
        "file_count": len(relatives),
        "bytes_scanned": byte_total,
        "truncated": len(relatives) >= MAX_FILES or byte_total >= MAX_TOTAL_BYTES,
        "languages": [{"name": k, "count": v} for k, v in languages.most_common(6)],
        "manifest_files": manifest_paths[:12],
        "readme_files": readmes,
        "license_files": license_files[:8],
        "entry_points": entry_points,
        "test_directories": test_dirs[:8],
        "contributor_instructions": contributor_instructions,
        "ci_files": ci_files[:8],
        "directories": sorted({str(Path(r).parent) if str(Path(r).parent) != "." else "." for r in relatives})[:24],
        "all_paths": relatives,
    }
    return {"repo_id": repo_id, "overview": overview, "candidates": candidates, "evidence": evidence, "checks": checks, "readme_text": readme_text[:20000], "task_tokens": sorted(tokens)[:40]}
