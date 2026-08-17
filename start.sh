#!/usr/bin/env bash
# Run Blackstart from the backend venv (LXC / bare metal).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT/backend"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
  .venv/bin/pip install -r requirements.txt
fi
export PYTHONPATH="$ROOT/backend"
export BLACKSTART_DATA_DIR="${BLACKSTART_DATA_DIR:-$ROOT/backend/data}"
mkdir -p "$BLACKSTART_DATA_DIR"
# Serve the built UI if present
if [[ -d "$ROOT/frontend/dist" && ! -d "$ROOT/backend/static" ]]; then
  ln -sfn "$ROOT/frontend/dist" "$ROOT/backend/static"
fi
exec .venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port "${BLACKSTART_PORT:-8088}"
