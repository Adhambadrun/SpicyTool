"""Email-OTP authentication + stateless HMAC sessions.

Flow: a user enters their name@bcflights.com address, we send a 6-digit OTP
via the Resend API, they verify it and receive a stateless HMAC session
token. Tokens are signed with a per-install secret; the client keeps them in
sessionStorage so closing the tab always ends the session.

Everything is in-memory on purpose (single-process deployment); the OTP store
is small, short-lived and rate-limited.
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

import httpx

try:  # optional: load backend/.env (git-ignored) for local runs
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass

# ------------------------------------------------------------------ config ---

ALLOWED_DOMAIN = "bcflights.com"
EMAIL_RE = re.compile(r"^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$")

OTP_TTL_SECONDS = 600        # a code is valid for 10 minutes
OTP_RESEND_COOLDOWN = 60     # seconds between sends to the same address
OTP_MAX_ATTEMPTS = 5         # wrong tries before the code is invalidated
SESSION_TTL_HOURS = 12       # session token lifetime

# Resend delivery credential — set RESEND_API_KEY in the environment or in
# backend/.env (git-ignored). Never hardcode it here: GitHub push protection
# blocks commits that contain live API keys.
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "")
RESEND_FROM = os.getenv("RESEND_FROM", "SpicyTool <noreply@bcflights.com>")
RESEND_API_URL = "https://api.resend.com/emails"

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


# --------------------------------------------------------------- OTP store ---

# email -> {"code": str, "expires": float, "attempts": int, "sent": float}
_OTPS: dict[str, dict] = {}


def validate_email(email: str) -> bool:
    email = (email or "").strip().lower()
    return bool(EMAIL_RE.match(email)) and email.endswith("@" + ALLOWED_DOMAIN)


async def _send_email(to: str, subject: str, text: str) -> tuple[bool, str]:
    """Send an email through Resend. Returns (ok, error_detail)."""
    if not RESEND_API_KEY:
        return False, "Email delivery is not configured on the server."
    payload = {"from": RESEND_FROM, "to": [to], "subject": subject, "text": text}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                RESEND_API_URL,
                json=payload,
                headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            )
    except httpx.HTTPError:
        return (
            False,
            "Could not reach the email service (api.resend.com). "
            "Check the server's network access and try again.",
        )
    if resp.status_code in (200, 201):
        return True, ""
    detail = ""
    try:
        detail = resp.json().get("message", "")
    except Exception:
        pass
    return False, f"Email service rejected the request ({resp.status_code}){': ' + detail if detail else ''}"


async def request_otp(email: str) -> tuple[bool, str, int]:
    """Generate + email a 6-digit code. Returns (ok, error, retry_after)."""
    email = email.strip().lower()
    entry = _OTPS.get(email)
    now = time.time()
    if entry and now - entry["sent"] < OTP_RESEND_COOLDOWN:
        return False, "A code was just sent. Please wait before requesting another.", int(
            OTP_RESEND_COOLDOWN - (now - entry["sent"])
        ) + 1
    code = f"{secrets.randbelow(1000000):06d}"
    ok, err = await _send_email(
        email,
        "Your SpicyTool verification code",
        f"Your SpicyTool verification code is {code}.\n\n"
        f"It expires in {OTP_TTL_SECONDS // 60} minutes. "
        "If you did not request it, you can ignore this email.",
    )
    if not ok:
        return False, err, 0
    _OTPS[email] = {"code": code, "expires": now + OTP_TTL_SECONDS, "attempts": 0, "sent": now}
    _gc_otps()
    return True, "", OTP_RESEND_COOLDOWN


def _gc_otps() -> None:
    now = time.time()
    for k in [k for k, v in _OTPS.items() if v["expires"] + 60 < now]:
        _OTPS.pop(k, None)


def verify_otp(email: str, code: str) -> str | None:
    """Check a code; returns a session token, or None when invalid."""
    email = (email or "").strip().lower()
    code = (code or "").strip()
    entry = _OTPS.get(email)
    if not entry or not code:
        return None
    now = time.time()
    if now > entry["expires"]:
        _OTPS.pop(email, None)
        return None
    if entry["attempts"] >= OTP_MAX_ATTEMPTS:
        _OTPS.pop(email, None)
        return None
    if not hmac.compare_digest(entry["code"], code):
        entry["attempts"] += 1
        return None
    _OTPS.pop(email, None)
    return issue_token(email)


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
