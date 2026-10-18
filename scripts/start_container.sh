#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PORT="${PORT:-10000}"
BACKEND_PORT="${PATCHTRACE_BACKEND_PORT:-8000}"

cd "$ROOT/backend"
/opt/patchtrace-venv/bin/uvicorn app.main:app --host 127.0.0.1 --port "$BACKEND_PORT" &
BACKEND_PID=$!

cd "$ROOT/frontend"
npm run start -- --hostname 0.0.0.0 --port "$PORT" &
FRONTEND_PID=$!

cleanup() {
  kill "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
  wait "$BACKEND_PID" "$FRONTEND_PID" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

# Fail fast if either process dies during startup; Render health check handles the rest.
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do
  sleep 2
done
exit 1
