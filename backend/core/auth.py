"""PIN authentication + stateless HMAC sessions.

Flow: the owner signs in with one of the two authorized addresses
(adhambadraan@icloud.com / adhambadraan@gmail.com) and the account PIN —
no email codes and no verification step. A correct PIN issues a stateless
HMAC session token signed with a per-install secret; the client keeps it
in sessionStorage so closing the tab always ends the session.

Failed PIN attempts are rate-limited in memory (single-process deployment):
5 wrong tries lock the address out for 60 seconds.

Configure via the environment (or backend/.env, git-ignored):
  LOGIN_PIN               the account PIN (default 141220)
  ALLOWED_LOGIN_EMAILS    comma-separated authorized addresses
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import time
from pathlib import Path

try:  # optional: load backend/.env (git-ignored) for local runs
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ------------------------------------------------------------------ config ---

DEFAULT_ALLOWED_EMAILS = "adhambadraan@icloud.com,adhambadraan@gmail.com"
ALLOWED_EMAILS = {
    e.strip().lower()
    for e in os.getenv("ALLOWED_LOGIN_EMAILS", DEFAULT_ALLOWED_EMAILS).split(",")
    if e.strip()
}
LOGIN_PIN = os.getenv("LOGIN_PIN", "141220").strip()
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$")

PIN_MAX_ATTEMPTS = 5         # wrong tries before the address is locked out
PIN_LOCKOUT_SECONDS = 60     # lockout duration
SESSION_TTL_HOURS = 12       # session token lifetime

_SECRET_FILE = Path(__file__).resolve().parent.parent / "data" / ".auth_secret"


def _secret() -> bytes:
    """Per-install signing secret (env override, else generated once on disk)."""
    env = os.getenv("AUTH_SECRET")
    if env:
        return env.encode()
    if _SECRET_FILE.exists():
        return _SECRET_FILE.read_bytes().strip()
    _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
    key = secrets.token_hex(32).encode()
    _SECRET_FILE.write_bytes(key)
    try:
        os.chmod(_SECRET_FILE, 0o600)
    except OSError:
        pass
    return key


# ------------------------------------------------------------- PIN attempts ---

# email -> {"fails": int, "locked_until": float}
_FAILS: dict[str, dict] = {}


def validate_email(email: str) -> bool:
    email = (email or "").strip().lower()
    return bool(EMAIL_RE.match(email)) and email in ALLOWED_EMAILS


def verify_pin(email: str, pin: str) -> tuple[str | None, str, int]:
    """Check the account PIN. Returns (token, error, retry_after).

    token is set on success; otherwise error describes the failure and
    retry_after (seconds) is set while the address is locked out.
    """
    email = (email or "").strip().lower()
    pin = (pin or "").strip()
    now = time.time()
    entry = _FAILS.get(email)
    if entry and entry.get("locked_until", 0) > now:
        return None, "Too many attempts — try again in a moment.", int(entry["locked_until"] - now) + 1
    if not email or not pin:
        return None, "Enter your email and PIN.", 0
    if not hmac.compare_digest(LOGIN_PIN, pin):
        entry = _FAILS.setdefault(email, {"fails": 0, "locked_until": 0.0})
        entry["fails"] += 1
        if entry["fails"] >= PIN_MAX_ATTEMPTS:
            entry["locked_until"] = now + PIN_LOCKOUT_SECONDS
            entry["fails"] = 0
            return None, "Too many attempts — try again in a minute.", PIN_LOCKOUT_SECONDS
        return None, "Incorrect PIN. Try again.", 0
    _FAILS.pop(email, None)
    return issue_token(email), "", 0


# ---------------------------------------------------------------- sessions ---

def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def issue_token(email: str) -> str:
    payload = {"email": email, "iat": int(time.time()), "exp": int(time.time()) + SESSION_TTL_HOURS * 3600}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    return f"v1.{body}.{sig}"


def verify_token(token: str | None) -> str | None:
    """Validate a session token; returns the email, or None."""
    if not token:
        return None
    parts = token.split(".")
    if len(parts) != 3 or parts[0] != "v1":
        return None
    _, body, sig = parts
    expect = hmac.new(_secret(), body.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expect, sig):
        return None
    try:
        payload = json.loads(_unb64(body))
        email = payload["email"]
        exp = int(payload["exp"])
    except Exception:
        return None
    if time.time() > exp or not validate_email(email):
        return None
    return email
