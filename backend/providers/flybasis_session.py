"""Supabase-session auth for the Flybasis award socket (operator's own account).

The Flybasis web app authenticates against Supabase (``sb.flybasis.com``):
a ``refresh_token`` (or ``email``+``password``) is exchanged for a
short-lived ``access_token`` (~1h), and that access_token is what the
Socket.IO middleware expects as ``auth={"token": ...}``.

This module is the **session** credential path. It is NOT a Flybasis-issued
API token: it uses the operator's own account session, consumes that
account's search quota, and automated use may conflict with Flybasis terms
of service. The official path (``FLYBASIS_API_KEY`` issued by Flybasis)
remains preferred and is checked first.

Env:
    FLYBASIS_SUPABASE_URL        default https://sb.flybasis.com
    FLYBASIS_SUPABASE_ANON_KEY   Supabase anon/publishable key
    FLYBASIS_REFRESH_TOKEN       refresh_token for the operator's account
    FLYBASIS_EMAIL / FLYBASIS_PASSWORD   password-grant fallback
    FLYBASIS_API2_URL            default https://api2.flybasis.com (quota lookup)
    FLYBASIS_REFRESH_FILE        override the rotated-token store path (tests)

HTTP only (no socket here). The access token is cached until ~30s before its
expiry; the rotated refresh token is persisted in the gitignored backend/data
dir so long-running deployments survive token rotation.

Why a client per exchange (not the shared engine): a refresh happens about
once per hour, so a throwaway ``httpx.AsyncClient`` costs nothing, and it
cannot hold keep-alive connections bound to a closed event loop (which
happens when the same process serves more than one loop — e.g. the socket
verifier runs each check in its own ``asyncio.run``).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import httpx

from core.http_engine import HttpEngine, ProviderError

SUPABASE_URL = "https://sb.flybasis.com"
SUPABASE_URL_ENV = "FLYBASIS_SUPABASE_URL"
ANON_KEY_ENV = "FLYBASIS_SUPABASE_ANON_KEY"
REFRESH_ENV = "FLYBASIS_REFRESH_TOKEN"
EMAIL_ENV = "FLYBASIS_EMAIL"
PASSWORD_ENV = "FLYBASIS_PASSWORD"
API2_URL_ENV = "FLYBASIS_API2_URL"
API2_URL = "https://api2.flybasis.com"
REFRESH_FILE_ENV = "FLYBASIS_REFRESH_FILE"

# Rotated refresh token cache (Supabase rotates refresh tokens on use).
# Overridable so tests/containers never dirty the repo or a shared volume.
def refresh_file() -> Path:
    explicit = env(REFRESH_FILE_ENV)
    if explicit:
        return Path(explicit)
    return Path(__file__).resolve().parent.parent / "data" / ".flybasis_refresh_token"

# Cached access token: (token, monotonic expiry).
_cache: tuple[str, float] | None = None
_CACHE_SKEW = 30.0  # refresh this far before the token actually expires


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def supabase_url() -> str:
    return env(SUPABASE_URL_ENV) or SUPABASE_URL


def anon_key() -> str | None:
    return env(ANON_KEY_ENV) or None


def refresh_token() -> str | None:
    value = env(REFRESH_ENV) or ""
    if not value:
        f = refresh_file()
        if f.exists():
            value = f.read_text(encoding="utf-8").strip()
    return value or None


def configured() -> bool:
    """True when a supabase-session credential is available (refresh or password)."""
    if refresh_token():
        return True
    return bool(env(EMAIL_ENV) and env(PASSWORD_ENV))


def session_source() -> str:
    return "Supabase refresh token" if refresh_token() else "Supabase email/password"


async def access_token(engine: HttpEngine | None = None) -> str:
    """Return a valid Supabase access token, refreshing when needed."""
    global _cache
    if _cache and _cache[1] > time.monotonic() + _CACHE_SKEW:
        return _cache[0]
    if not configured():
        raise ProviderError(
            "Flybasis session mode is not configured. Set FLYBASIS_REFRESH_TOKEN "
            f"(or {EMAIL_ENV} + {PASSWORD_ENV}) and {ANON_KEY_ENV}."
        )
    headers = {
        "apikey": anon_key() or "",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    refresh = refresh_token()
    if refresh:
        params = {"grant_type": "refresh_token"}
        body: dict[str, str] = {"refresh_token": refresh}
    else:
        params = {"grant_type": "password"}
        body = {"email": env(EMAIL_ENV), "password": env(PASSWORD_ENV)}
    url = f"{supabase_url().rstrip('/')}/auth/v1/token"
    try:
        resp = await _post(engine, url, headers=headers, params=params, json=body)
    except ProviderError:
        raise
    except Exception as exc:  # noqa: BLE001 — surface as actionable
        raise ProviderError(
            f"Flybasis session auth unreachable: {exc.__class__.__name__}"
        ) from exc
    if resp.status_code in (400, 401, 403):
        raise ProviderError(
            "Flybasis session login was rejected (the refresh token was "
            "rotated/expired, or email+password are wrong). Re-capture it from "
            "your browser (Network -> auth/v1/token -> refresh_token) and set "
            f"{REFRESH_ENV}."
        )
    if resp.status_code >= 400:
        raise ProviderError(f"Flybasis session auth error HTTP {resp.status_code}")
    data = resp.json() if hasattr(resp, "json") else {}
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise ProviderError("Flybasis session auth returned no access_token")
    new_refresh = str(data.get("refresh_token") or "").strip()
    if new_refresh and new_refresh != refresh:
        _persist_refresh(new_refresh)
    expires_in = float(data.get("expires_in") or 3600)
    _cache = (token, time.monotonic() + expires_in)
    return token


def _persist_refresh(token: str) -> None:
    try:
        f = refresh_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(token, encoding="utf-8")
    except OSError:
        pass  # cache-only; next refresh still works from the env value


def reset_cache() -> None:
    """Drop the cached access token (tests / after a rotated secret)."""
    global _cache
    _cache = None


async def _post(engine: HttpEngine | None, url: str, **kwargs):
    """POST via the injected engine, or a throwaway client (see module docstring)."""
    if engine is not None:
        return await engine.request("POST", url, **kwargs)
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(url, **kwargs)


async def searches_remaining(engine: HttpEngine | None = None) -> int | None:
    """Best-effort account quota (maxSearchesRemaining), or None when unknown."""
    token = await access_token(engine)
    try:
        resp = await _post(
            engine,
            f"{(env(API2_URL_ENV) or API2_URL).rstrip('/')}/trpc/user.whoami?batch=1",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json() if hasattr(resp, "json") else {}
        rows = data if isinstance(data, list) else [data]
        for row in rows:
            result = row.get("result", {}) if isinstance(row, dict) else {}
            inner = result.get("data", {}) if isinstance(result, dict) else {}
            value = inner.get("maxSearchesRemaining")
            if isinstance(value, int):
                return value
    except Exception:  # noqa: BLE001 — quota is informational only
        return None
    return None
