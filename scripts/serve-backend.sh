#!/usr/bin/env bash
# FastAPI backend — run from repo root so `backend.main` imports correctly.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${ROOT}/.venv/bin/python"
if [[ ! -x "$PY" ]]; then
  echo "Missing ${PY}. Create venv and install backend/requirements.txt first." >&2
  exit 1
fi
exec "$PY" -m uvicorn backend.main:app --reload --host 127.0.0.1 --port 8000
