#!/usr/bin/env bash
# SpicyTool — no-Docker path: venv + uvicorn on 0.0.0.0:8000
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  echo ">> creating virtualenv (.venv)"
  python3 -m venv .venv
fi

echo ">> installing requirements"
.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet -r backend/requirements.txt

# Load .env if present (does not override the environment)
if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  source .env
  set +a
fi

echo ">> SpicyTool listening on http://0.0.0.0:8000"
cd backend
exec ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
