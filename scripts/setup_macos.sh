#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
need_cmd() { command -v "$1" >/dev/null 2>&1 || { echo "Missing required command: $1"; exit 1; }; }
need_cmd python3
need_cmd node
need_cmd npm
PYTHON_BIN=""
for candidate in python3.12 python3.11 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    version="$($candidate -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3,11) else 1)'; then PYTHON_BIN="$candidate"; break; fi
  fi
done
if [[ -z "$PYTHON_BIN" ]]; then echo "PatchTrace requires Python 3.11+. Install Python 3.12 with: brew install python@3.12"; exit 1; fi
node -e 'const [a,b]=process.versions.node.split(".").map(Number); if(a<20 || (a===20 && b<9)){console.error("PatchTrace requires Node.js 20.9 or newer. Install a current LTS Node.js release."); process.exit(1)}'
echo "Using $($PYTHON_BIN --version) and Node $(node --version)"
if [[ ! -d "$ROOT/backend/.venv" ]]; then "$PYTHON_BIN" -m venv "$ROOT/backend/.venv"; fi
"$ROOT/backend/.venv/bin/python" -m pip install --upgrade pip
"$ROOT/backend/.venv/bin/pip" install -r "$ROOT/backend/requirements.txt"
if [[ ! -f "$ROOT/backend/.env" ]]; then cp "$ROOT/backend/.env.example" "$ROOT/backend/.env"; fi
(
  cd "$ROOT/frontend"
  npm install
)
chmod +x "$ROOT/scripts/start_macos.sh"
echo
echo "Setup complete. Start PatchTrace with: ./scripts/start_macos.sh"
