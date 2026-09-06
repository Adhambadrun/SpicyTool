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
import time
from pathlib import Path

try:  # optional: load .env (git-ignored) for local runs
    from dotenv import load_dotenv

    # Both documented locations are honoured, root first so a repo-root .env
    # works when the app is started directly (not just via run.sh). Neither
    # overrides a variable already present in the real environment.
    _repo_root = Path(__file__).resolve().parent.parent.parent
    load_dotenv(_repo_root / ".env")
    load_dotenv(_repo_root / "backend" / ".env")
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
SESSION_TTL_HOURS = 720       # 30 days — a tab left open shouldn't expire mid-use

_SECRET_FILE = Path(__file__).resolve().parent.parent / "data" / ".auth_secret"
_RUNTIME_SECRET: bytes | None = None  # in-memory fallback (read-only filesystems)


def _derive_secret() -> bytes:
    """Deterministic fallback signing secret for serverless platforms.

    On Vercel the filesystem is ephemeral, so a randomly generated
    ``.auth_secret`` would change on every cold start and instantly
    invalidate every token that was just issued — the client sees constant
    401s and keeps getting bounced back to login mid-session.

    Deriving the key from the stable login configuration (PIN + authorized
    addresses + a fixed app namespace) makes the same secret reproducible
    across every instance and cold start, so sessions stay valid for the
    life of the tab. Set ``AUTH_SECRET`` to a long random value for a
    stronger, deployment-agnostic key; this fallback guarantees session
    *stability* only.
    """
    seed = "SpicyTool::v1::" + LOGIN_PIN + "::" + ",".join(sorted(ALLOWED_EMAILS))
    return hashlib.sha256(seed.encode()).digest()


def _secret() -> bytes:
    """Per-install signing secret.

    Order: AUTH_SECRET env var -> backend/data/.auth_secret (generated once)
    -> deterministic key derived from the login config. The last case keeps
    sessions valid on read-only/ephemeral filesystems (serverless platforms
    such as Vercel) so users are not logged out between cold starts.
    """
    global _RUNTIME_SECRET
    env = os.getenv("AUTH_SECRET")
    if env:
        return env.encode()
    if _RUNTIME_SECRET is not None:
        return _RUNTIME_SECRET
    try:
        if _SECRET_FILE.exists():
            _RUNTIME_SECRET = _SECRET_FILE.read_bytes().strip()
            if _RUNTIME_SECRET:
                return _RUNTIME_SECRET
    except OSError:
        pass
    key = _derive_secret()
    try:
        _SECRET_FILE.parent.mkdir(parents=True, exist_ok=True)
        _SECRET_FILE.write_bytes(key)
        try:
            os.chmod(_SECRET_FILE, 0o600)
        except OSError:
            pass
    except OSError:
        # Read-only filesystem: the deterministic key still holds across
        # instances, so sessions remain valid without the file.
        pass
    _RUNTIME_SECRET = key
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
    # compare_digest on str raises TypeError for non-ASCII input; comparing
    # bytes accepts anything and simply fails the match (a PIN is digits).
    if not hmac.compare_digest(LOGIN_PIN.encode(), pin.encode()):
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
    # compare_digest on str raises TypeError for non-ASCII input; the token is
    # attacker-controlled (Authorization header), so compare bytes and let any
    # malformed signature simply fail the match.
    if not hmac.compare_digest(expect.encode(), sig.encode()):
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
