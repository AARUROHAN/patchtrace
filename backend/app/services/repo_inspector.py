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
TEXT_EXTS = {
    ".py", ".md", ".rst", ".txt", ".toml", ".yaml", ".yml", ".json",
    ".js", ".jsx", ".ts", ".tsx", ".css", ".html", ".sh", ".ini", ".cfg",
    ".go", ".rs", ".java", ".rb", ".sql",
    # Native source extensions are essential for C/C++ and mixed-language monorepos.
    ".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx", ".m", ".mm",
    ".cmake", ".gradle", ".xml", ".properties", ".swift", ".kt", ".kts", ".cs",
}
STOPWORDS = {"this", "that", "with", "from", "have", "what", "when", "where", "which", "would", "could", "should", "about", "into", "file", "files", "issue", "task", "please", "help", "make", "need", "does", "not", "and", "the", "for", "are", "was", "were", "you", "your", "our", "can", "how", "why", "has", "its", "readme", "repo", "repository"}


def _is_test_surface(relative: str) -> bool:
    """Recognise common test directories and file names across Python, C++, and monorepos."""
    path = Path(relative)
    test_component = re.compile(r"(?:^|[_-])(?:unit[_-]?)?tests?(?:$|[_-])", flags=re.IGNORECASE)
    if any(test_component.search(part.lower()) for part in path.parts[:-1]):
        return True
    stem = path.stem.lower()
    return bool(test_component.search(stem))


def _copy_local_snapshot(source: Path, destination: Path) -> None:
    """Copy a bounded, useful snapshot with project metadata and source before bulky docs."""
    destination.mkdir(parents=True, exist_ok=True)
    candidates: list[tuple[int, int, str, str, Path, int]] = []
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

            candidates.append((priority, len(parts), relative.lower(), relative, src, size))

    # Deterministic ordering avoids alphabetical traversal accidentally scanning docs
    # and examples while omitting the project's actual source language.
    candidates.sort(key=lambda item: (item[0], item[1], item[2]))
    total = 0
    count = 0
    for _, _, _, relative, src, size in candidates:
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

    checks: list[dict[str, Any]] = [{"id": "inventory", "label": "Repository inventory", "command": "Static file scan (no code execution)", "type": "static", "status": "passed", "detail": f"Scanned {len(relatives)} supported text/code files ({byte_total:,} bytes within size limits)."}]
    py_files = [r for r in relatives if r.endswith(".py")]
    test_files = [r for r in relatives if _is_test_surface(r)]
    if py_files:
        checks.append({"id": "python_compile", "label": "Python syntax compilation", "command": "python -m compileall -q .", "type": "safe-compile", "status": "not_run", "detail": "Available: compiles Python sources without importing the project. Run explicitly to record a real result."})
    if test_files:
        checks.append({"id": "pytest", "label": "Python test suite", "command": "python -m pytest -q", "type": "exec-tests", "status": "not_run", "detail": "Available: executes repository tests. Only run for a repository you trust, after confirming the warning."})
    checks.append({"id": "model", "label": "AI-assisted task analysis", "command": "Gemma API (hosted) or explicitly configured Ollama", "type": "model", "status": "not_run", "detail": "Marked RESPONDED when the configured model returns a usable response; this does not verify a proposed fix."})

    overview = {
        "repo_id": repo_id,
        "source_label": source_label,
        "file_count": len(relatives),
        "bytes_scanned": byte_total,
        "truncated": len(relatives) >= MAX_FILES or byte_total >= MAX_TOTAL_BYTES,
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
