from __future__ import annotations

import os
import shutil
import time
from collections import defaultdict, deque
from pathlib import Path
from threading import Lock
from typing import Any
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.schemas import CheckRequest, InspectRequest
from app.services.artifacts import make_artifact, propose_readme_diff
from app.services.check_runner import run_check
from app.services.ai_provider import analyze_task, model_config
from app.services.repo_inspector import DEMO_REPO, WORKSPACE, inspect_repository, prepare_repository

PUBLIC_MODE = os.getenv("PATCHTRACE_PUBLIC_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
CORS_ORIGINS = [origin.strip() for origin in os.getenv(
    "PATCHTRACE_CORS_ORIGINS", "http://localhost:3000,http://127.0.0.1:3000"
).split(",") if origin.strip()]
RATE_LIMIT_PER_MINUTE = max(1, int(os.getenv("PATCHTRACE_MAX_REQUESTS_PER_MINUTE", "8")))
MAX_SESSIONS = max(3, int(os.getenv("PATCHTRACE_MAX_SESSIONS", "20")))
_RATE_WINDOW: dict[str, deque[float]] = defaultdict(deque)
_RATE_LOCK = Lock()
SESSIONS: dict[str, dict[str, Any]] = {}

app = FastAPI(title="PatchTrace API", version="0.2.0", description="Evidence-backed open-source contribution workflow.", docs_url="/api/docs", redoc_url="/api/redoc", openapi_url="/api/openapi.json")
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=False,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Content-Type"],
)


@app.middleware("http")
async def public_rate_limit(request: Request, call_next):
    # Lightweight single-process protection for a public demo. Use platform-level
    # quotas/WAF for stronger multi-instance controls. It never rate-limits reads.
    if PUBLIC_MODE and request.method == "POST" and (request.url.path == "/api/inspect" or request.url.path.startswith("/api/checks/")):
        client_id = "public-demo-global"  # Next.js proxies all production calls from the same container.
        now = time.monotonic()
        with _RATE_LOCK:
            bucket = _RATE_WINDOW[client_id]
            while bucket and now - bucket[0] >= 60:
                bucket.popleft()
            if len(bucket) >= RATE_LIMIT_PER_MINUTE:
                return JSONResponse(status_code=429, content={"detail": f"Demo rate limit reached ({RATE_LIMIT_PER_MINUTE} requests per minute). Please wait a minute and retry."})
            bucket.append(now)
            if len(_RATE_WINDOW) > 1000:
                for key in list(_RATE_WINDOW)[:200]:
                    if not _RATE_WINDOW[key] or now - _RATE_WINDOW[key][-1] >= 60:
                        _RATE_WINDOW.pop(key, None)
    return await call_next(request)


@app.get("/api/health")
def health() -> dict[str, Any]:
    return {"status": "ok", "product": "PatchTrace", "mode": "public-demo" if PUBLIC_MODE else "local-development", "public_mode": PUBLIC_MODE, "model": model_config(), "persistence": "in-memory session only"}


@app.get("/api/demo")
def demo() -> dict[str, Any]:
    return {"source_type": "demo", "source": "", "source_label": "Synthetic demo repository (included with PatchTrace)",
            "recommended_task": "The README quick-start command references a script that is missing. Propose a reviewable documentation correction without changing application logic.",
            "synthetic": True, "model": model_config(), "public_mode": PUBLIC_MODE}


@app.post("/api/inspect")
def inspect(request: InspectRequest) -> dict[str, Any]:
    repo_id = ""
    root: Path | None = None
    try:
        if PUBLIC_MODE and request.source_type == "local":
            raise ValueError("Local filesystem paths are disabled on the public deployment. Use the synthetic demo or a public GitHub repository.")
        repo_id, root, source_label = prepare_repository(request.source_type, request.source)
        analysis = inspect_repository(root, repo_id, source_label, request.task)
        model_result: dict[str, Any] | None = None
        model_error: str | None = None
        if request.use_ai:
            model_result, model_error = analyze_task(request.task, analysis["overview"], analysis["candidates"], analysis["evidence"])
        # Keep model references bounded to paths in the snapshot; no unseen path may become an evidence card.
        if model_result:
            allowed = set(analysis["overview"]["all_paths"])
            model_paths = set(model_result.get("likely_files", []))
            for path in model_paths:
                if path in allowed and not any(item["path"] == path for item in analysis["candidates"]):
                    analysis["candidates"].append({"path": path, "score": 1, "reason": "Suggested by local model; the file exists in the scanned manifest. Verify the cited content."})
            analysis["candidates"] = analysis["candidates"][:10]
            for path in model_paths:
                if path in allowed:
                    from app.services.repo_inspector import _line_evidence
                    analysis["evidence"].extend(_line_evidence(root / path, path, set(analysis["task_tokens"]), request.task)[:1])
            # Remove duplicate (path,line) citations.
            seen: set[tuple[str, int]] = set()
            analysis["evidence"] = [e for e in analysis["evidence"] if not ((e["path"], e["line"]) in seen or seen.add((e["path"], e["line"])))][:12]
        for check in analysis["checks"]:
            if check["id"] == "model":
                provider = model_config()
                check["can_run"] = bool(provider.get("configured"))
                if model_result is not None and not model_result.get("format_warning"):
                    check["status"] = "passed"
                    check["detail"] = f"{provider.get('provider')} returned a structured advisory analysis. This confirms a model response, not that a proposed fix is correct."
                elif model_result is not None:
                    check["status"] = "failed"
                    check["detail"] = "The model responded, but did not return the required structured format. A partial response was retained with a warning."
                elif not request.use_ai:
                    check["status"] = "not_run"
                    check["detail"] = "AI analysis was switched off for this run."
                elif not provider.get("configured"):
                    check["status"] = "not_run"
                    check["detail"] = model_error or "AI provider is not configured; no live model request was made."
                else:
                    check["status"] = "failed"
                    check["detail"] = model_error or ("The configured model request failed; deterministic analysis was retained.")
        for check in analysis["checks"]:
            if check.get("id") == "pytest" and PUBLIC_MODE:
                check["can_run"] = False
                check["detail"] = "Disabled on the public deployment: this would execute code from the selected repository. Use a disposable container locally instead."
            elif check.get("id") == "python_compile":
                check["can_run"] = True
            elif check.get("id") != "model":
                check.setdefault("can_run", True)
        diff = propose_readme_diff(root, analysis["overview"], request.task, analysis["candidates"])
        artifact = make_artifact(request.task, analysis, model_result, diff)
        if len(SESSIONS) >= MAX_SESSIONS:
            oldest_id = next(iter(SESSIONS))
            SESSIONS.pop(oldest_id)
            shutil.rmtree(WORKSPACE / oldest_id, ignore_errors=True)
        SESSIONS[repo_id] = {"root": root, "analysis": analysis, "task": request.task, "model_result": model_result, "artifact": artifact, "diff": diff, "created_at": time.time()}
        return {
            "repo_id": repo_id,
            "task": request.task,
            "overview": {k: v for k, v in analysis["overview"].items() if k != "all_paths"},
            "candidates": analysis["candidates"],
            "evidence": analysis["evidence"],
            "checks": analysis["checks"],
            "artifact": artifact,
            "proposed_diff": diff,
            "ai": {"enabled": request.use_ai, "used": model_result is not None, "configured": bool(model_config().get("configured")),
                   "provider": model_config().get("provider"), "model": model_config().get("model"), "error": model_error,
                   "format_warning": (model_result or {}).get("format_warning"),
                   "summary": (model_result or {}).get("summary") or "Gemma did not return an analysis for this run; deterministic repository heuristics produced the initial plan.",
                   "plan": (model_result or {}).get("plan", []), "questions": (model_result or {}).get("questions", []), "risks": (model_result or {}).get("risks", []),
                   "notice": "AI suggestions remain advisory; paths are validated against the scanned manifest and test results come only from executed checks."},
            "coverage": {"status": "partial" if analysis["overview"]["truncated"] else "bounded", "files_scanned": analysis["overview"]["file_count"], "bytes_scanned": analysis["overview"]["bytes_scanned"],
                         "warning": "PatchTrace scans bounded text/code files only. Binary files, dependency directories and hidden folders are skipped."},
        }
    except ValueError as exc:
        if repo_id:
            shutil.rmtree(WORKSPACE / repo_id, ignore_errors=True)
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        if repo_id:
            shutil.rmtree(WORKSPACE / repo_id, ignore_errors=True)
        raise HTTPException(status_code=500, detail=f"Inspection failed safely: {type(exc).__name__}: {str(exc)[:300]}") from exc


@app.post("/api/checks/{repo_id}")
def run_repository_check(repo_id: str, request: CheckRequest) -> dict[str, Any]:
    session = SESSIONS.get(repo_id)
    if not session:
        raise HTTPException(status_code=404, detail="This repository session is no longer available. Inspect the repository again.")
    if PUBLIC_MODE and request.check_id == "pytest":
        return {"id": "pytest", "status": "not_run", "command": "python -m pytest -q", "detail": "Disabled on the public deployment because tests can execute arbitrary repository code.", "return_code": None, "output": ""}
    return run_check(session["root"], request.check_id, request.confirm_execution)
