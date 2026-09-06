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

# core.auth loads root .env then backend/.env via python-dotenv when the app
# imports. Do not source secrets as shell code here: that also used to override
# exported deployment credentials despite claiming not to. Python's loader
# preserves the process environment and never executes values from a HAR.

echo ">> SpicyTool listening on http://0.0.0.0:8000"
cd backend
exec ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
