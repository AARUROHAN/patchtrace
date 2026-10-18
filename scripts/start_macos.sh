#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
[[ -x "$ROOT/backend/.venv/bin/uvicorn" ]] || { echo "Backend environment missing. Run ./scripts/setup_macos.sh first."; exit 1; }
[[ -d "$ROOT/frontend/node_modules" ]] || { echo "Frontend dependencies missing. Run ./scripts/setup_macos.sh first."; exit 1; }
if lsof -nP -iTCP:8000 -sTCP:LISTEN >/dev/null 2>&1; then echo "Port 8000 is already in use. Stop the other backend or change its port first."; exit 1; fi
if lsof -nP -iTCP:3000 -sTCP:LISTEN >/dev/null 2>&1; then echo "Port 3000 is already in use. Stop the other frontend or change its port first."; exit 1; fi
mkdir -p "$ROOT/.logs"
cleanup() { [[ -n "${BACKEND_PID:-}" ]] && kill "$BACKEND_PID" 2>/dev/null || true; [[ -n "${FRONTEND_PID:-}" ]] && kill "$FRONTEND_PID" 2>/dev/null || true; wait 2>/dev/null || true; }
trap cleanup EXIT INT TERM
(
  cd "$ROOT/backend"
  set -a; [[ -f .env ]] && source .env; set +a
  exec .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
) > "$ROOT/.logs/backend.log" 2>&1 & BACKEND_PID=$!
(
  cd "$ROOT/frontend"
  exec npm run dev -- --hostname 127.0.0.1 --port 3000
) > "$ROOT/.logs/frontend.log" 2>&1 & FRONTEND_PID=$!
for i in {1..40}; do
  if curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1 && curl -fsS http://127.0.0.1:3000 >/dev/null 2>&1; then break; fi
  sleep 1
done
if ! curl -fsS http://127.0.0.1:8000/api/health >/dev/null 2>&1; then echo "Backend did not start; see $ROOT/.logs/backend.log"; tail -50 "$ROOT/.logs/backend.log"; exit 1; fi
if ! curl -fsS http://127.0.0.1:3000 >/dev/null 2>&1; then echo "Frontend did not start; see $ROOT/.logs/frontend.log"; tail -50 "$ROOT/.logs/frontend.log"; exit 1; fi
echo "PatchTrace is running."
echo "Frontend: http://localhost:3000"
echo "Backend:  http://localhost:8000/api/health"
echo "API docs: http://localhost:8000/api/docs"
echo "Logs:     $ROOT/.logs/backend.log and frontend.log"
echo "Press Ctrl+C to stop both servers."
while kill -0 "$BACKEND_PID" 2>/dev/null && kill -0 "$FRONTEND_PID" 2>/dev/null; do sleep 2; done
echo "A server stopped. Check the log files above."
