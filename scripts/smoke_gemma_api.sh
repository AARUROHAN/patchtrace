#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -f "$ROOT/backend/.env" ]] || { echo "Missing backend/.env. Copy backend/.env.example and add GEMINI_API_KEY first."; exit 1; }
[[ -x "$ROOT/backend/.venv/bin/python" ]] || { echo "Missing backend/.venv. Run ./scripts/setup_macos.sh first."; exit 1; }
(
  cd "$ROOT/backend"
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
  PYTHONPATH="$ROOT/backend" "$ROOT/backend/.venv/bin/python" - <<'PY'
from app.services.ai_provider import analyze_task, model_config

config = model_config()
print(f"Provider: {config.get('provider')} | Model: {config.get('model')} | Key configured: {config.get('configured')}")
if not config.get("configured"):
    raise SystemExit("Gemma API is not configured. Set GEMINI_API_KEY in backend/.env.")
result, error = analyze_task(
    "Identify the next safe step for a contributor when README instructions and repository files disagree.",
    {"source_label": "Synthetic smoke-test repository", "file_count": 2, "all_paths": ["README.md", "src/main.py"]},
    [{"path": "README.md", "score": 5, "reason": "Contains setup instructions"}],
    [{"path": "README.md", "line": 12, "text": "Run python app.py", "relevance": 3}],
)
if error or not result:
    raise SystemExit(f"Gemma API smoke test FAILED: {error or 'No valid response'}")
print("Gemma API smoke test PASSED")
print("Model summary:", result["summary"])
print("Likely files:", ", ".join(result["likely_files"]) or "None")
print("Reminder: this proves API response and parsing only, not fix correctness.")
PY
)
