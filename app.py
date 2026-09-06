"""Vercel entrypoint for SpicyTool.

Vercel's Python runtime looks for a FastAPI instance named ``app`` in a
root-level ``app.py`` (see https://vercel.com/docs/frameworks/backend/fastapi).
The real application lives in ``backend/main.py`` and uses top-level imports
(``from core import auth`` ...), so we put ``backend/`` on ``sys.path`` and
re-export the FastAPI instance. Every route — ``/`` (frontend), ``/api/v1/*``
and ``/api/v2/*`` — is then served by this single function.

Local runs are unchanged: ``./run.sh`` / ``docker compose up`` still start
``backend/main.py`` directly.
"""
from __future__ import annotations

import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent / "backend"
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

from main import app  # noqa: E402,F401  (re-exported for Vercel)
