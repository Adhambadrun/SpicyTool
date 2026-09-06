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

# Load .env if present (does not override the environment).
# Root .env is the primary location; backend/.env is accepted too so the
# documented "put FLYBASIS_API_KEY in backend/.env" path always works.
for envfile in .env backend/.env; do
  if [ -f "$envfile" ]; then
    set -a
    # shellcheck disable=SC1091
    source "$envfile"
    set +a
  fi
done

echo ">> SpicyTool listening on http://0.0.0.0:8000"
cd backend
exec ../.venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
