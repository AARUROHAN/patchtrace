from __future__ import annotations

import json
import os
from typing import Any
import httpx

OLLAMA_URL = os.getenv("PATCHTRACE_OLLAMA_URL", "http://localhost:11434").rstrip("/")
OLLAMA_MODEL = os.getenv("PATCHTRACE_OLLAMA_MODEL", "").strip()


def model_config() -> dict[str, Any]:
    return {"configured": bool(OLLAMA_MODEL), "provider": "Ollama local inference", "model": OLLAMA_MODEL or None, "base_url": OLLAMA_URL}


def analyze_task(task: str, overview: dict[str, Any], candidates: list[dict[str, Any]], evidence: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not OLLAMA_MODEL:
        return None
    allowed_paths = overview.get("all_paths", [])
    prompt = f'''You are PatchTrace, an open-weight-model assistant for open-source contribution workflows.
Use ONLY the supplied repository facts. Never claim code was run or a fix verified. Do not invent files, commands, API routes or test results. Select likely file paths only from ALLOWED_PATHS. Return strict JSON with keys: summary (string), likely_files (array of exact paths), plan (array of 3-6 concrete strings), questions (array of strings), risks (array of strings). If evidence is insufficient, say so.
TASK:
{task}
REPOSITORY OVERVIEW:
{json.dumps({k:v for k,v in overview.items() if k not in {'all_paths'}}, ensure_ascii=False)[:8000]}
ALLOWED_PATHS:
{json.dumps(allowed_paths[:400])}
CANDIDATES:
{json.dumps(candidates[:8])}
LINE EVIDENCE:
{json.dumps(evidence[:12], ensure_ascii=False)}
'''
    try:
        response = httpx.post(
            f"{OLLAMA_URL}/api/generate",
            json={"model": OLLAMA_MODEL, "prompt": prompt, "stream": False, "format": "json", "options": {"temperature": 0.1}},
            timeout=75,
        )
        response.raise_for_status()
        data = response.json()
        raw = data.get("response", "")
        obj = json.loads(raw)
        if not isinstance(obj, dict):
            return None
        obj["likely_files"] = [p for p in obj.get("likely_files", []) if p in allowed_paths][:8]
        obj["plan"] = [str(v)[:500] for v in obj.get("plan", [])][:8]
        obj["questions"] = [str(v)[:400] for v in obj.get("questions", [])][:5]
        obj["risks"] = [str(v)[:400] for v in obj.get("risks", [])][:5]
        obj["model"] = OLLAMA_MODEL
        return obj
    except (httpx.HTTPError, ValueError, KeyError, TypeError):
        return None
