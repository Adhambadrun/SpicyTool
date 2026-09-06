"""Supabase-session auth for the Flybasis award socket (operator's own account).

The Flybasis web app exchanges a refresh token (or email/password) at
``sb.flybasis.com`` for the short-lived Socket.IO ``auth={"token": ...}``.
The official ``FLYBASIS_API_KEY`` path remains preferred. Session mode uses
that account's quota; operators must have permission to automate its use.

Required: FLYBASIS_SUPABASE_ANON_KEY and either FLYBASIS_REFRESH_TOKEN or
FLYBASIS_EMAIL + FLYBASIS_PASSWORD. Optional endpoint overrides:
FLYBASIS_SUPABASE_URL / FLYBASIS_API2_URL.

Refresh env values are bootstrap credentials, NOT the current token after
Supabase rotates it. Keep the latest rotation in memory and in an atomic,
private FLYBASIS_REFRESH_FILE, bound to the originating configuration. A new
env credential invalidates both old caches. Concurrent searches in one event
loop share one exchange, rather than spending the same refresh token twice.

The default file is backend/data/.flybasis_refresh_token (gitignored), or
/tmp/spicytool/.flybasis_refresh_token on Vercel. Files and locks are local to
ONE instance: serverless cold starts/replicas do not share refresh state. Use
an official API key for production, or independent password-grant sessions;
a refresh token in Vercel env alone is not durable multi-instance auth.

HTTP clients are per exchange to avoid reusing pools from closed event loops
(CLI checks use multiple asyncio.run calls). No response body or credential
is included in errors.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
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

_cache: tuple[str, float] | None = None  # access token, monotonic expiry
_context_key: str | None = None
_rotated_refresh: str | None = None
_refresh_lock: asyncio.Lock | None = None
_lock_loop: asyncio.AbstractEventLoop | None = None
_CACHE_SKEW = 30.0


def env(name: str) -> str:
    return (os.environ.get(name) or "").strip()


def refresh_file() -> Path:
    if explicit := env(REFRESH_FILE_ENV):
        return Path(explicit).expanduser()
    if env("VERCEL"):
        return Path(tempfile.gettempdir()) / "spicytool" / ".flybasis_refresh_token"
    return Path(__file__).resolve().parent.parent / "data" / ".flybasis_refresh_token"


def supabase_url() -> str:
    return (env(SUPABASE_URL_ENV) or SUPABASE_URL).rstrip("/")


def anon_key() -> str | None:
    return env(ANON_KEY_ENV) or None


def _ensure_context() -> str:
    """Bind rotations/cache to the configured account, origin and token store."""
    global _context_key, _cache, _rotated_refresh
    config = [supabase_url(), anon_key(), env(REFRESH_ENV), env(EMAIL_ENV),
              env(PASSWORD_ENV), str(refresh_file().absolute())]
    key = hashlib.sha256(json.dumps(config).encode()).hexdigest()
    if key != _context_key:
        _cache = None
        _rotated_refresh = None
        _context_key = key
    return key


def _stored_refresh(context: str) -> str | None:
    try:
        raw = refresh_file().read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return None  # missing/unreadable/read-only storage must not break auth
    if not raw:
        return None
    try:
        stored = json.loads(raw)
    except ValueError:
        # Legacy plain-token files have no account binding. Only use one when
        # no explicit env credential could accidentally be overridden by it.
        if not (env(REFRESH_ENV) or env(EMAIL_ENV) or env(PASSWORD_ENV)):
            return raw
        return None
    if not isinstance(stored, dict) or stored.get("context") != context:
        return None
    token = stored.get("refresh_token")
    return token.strip() if isinstance(token, str) and token.strip() else None


def refresh_token() -> str | None:
    context = _ensure_context()
    return _rotated_refresh or _stored_refresh(context) or env(REFRESH_ENV) or None


def configured() -> bool:
    """Both the Supabase app key and an account credential are required."""
    return bool(anon_key() and (refresh_token() or (env(EMAIL_ENV) and env(PASSWORD_ENV))))


def session_source() -> str:
    return "Supabase refresh token" if refresh_token() else "Supabase email/password"


def _lock() -> asyncio.Lock:
    global _refresh_lock, _lock_loop
    loop = asyncio.get_running_loop()
    if _refresh_lock is None or _lock_loop is not loop:
        _refresh_lock = asyncio.Lock()
        _lock_loop = loop
    return _refresh_lock


async def access_token(engine: HttpEngine | None = None) -> str:
    """Return an access token; at most one refresh per instance/event loop."""
    global _cache, _rotated_refresh
    async with _lock():
        _ensure_context()
        if not configured():
            raise ProviderError(
                "Flybasis session mode is not configured. Set "
                f"{ANON_KEY_ENV} and {REFRESH_ENV} "
                f"(or {EMAIL_ENV} + {PASSWORD_ENV})."
            )
        if _cache and _cache[1] > time.monotonic() + _CACHE_SKEW:
            return _cache[0]
        headers = {
            "apikey": anon_key() or "",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        refresh = refresh_token()
        if refresh:
            params = {"grant_type": "refresh_token"}
            body = {"refresh_token": refresh}
        else:
            params = {"grant_type": "password"}
            body = {"email": env(EMAIL_ENV), "password": env(PASSWORD_ENV)}
        try:
            resp = await _post(
                engine, f"{supabase_url()}/auth/v1/token",
                headers=headers, params=params, json=body,
            )
        except Exception as exc:  # no upstream body/credential in public errors
            raise ProviderError(
                f"Flybasis session auth unreachable: {exc.__class__.__name__}"
            ) from None
        if resp.status_code in (400, 401, 403):
            raise ProviderError(
                "Flybasis session login was rejected. The refresh token may "
                "have expired, rotated in another instance, or been revoked; "
                "email/password may be incorrect. Sign in again and privately "
                f"replace {REFRESH_ENV} with the latest auth response's "
                "refresh_token, or update your password-grant credentials."
            )
        if not 200 <= resp.status_code < 300:
            raise ProviderError(f"Flybasis session auth error HTTP {resp.status_code}")
        try:
            data = resp.json()
            if not isinstance(data, dict):
                raise ValueError
            token = data.get("access_token")
            new_refresh = data.get("refresh_token")
            expires_in = float(data.get("expires_in", 3600))
            if not isinstance(token, str) or not token.strip():
                raise ValueError
            if new_refresh is not None and not isinstance(new_refresh, str):
                raise ValueError
            if not math.isfinite(expires_in) or expires_in <= 0:
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            raise ProviderError("Flybasis session auth returned an invalid token response") from None

        # Keep rotations even if storage is unavailable (e.g. a read-only
        # serverless filesystem). Never fall back to the spent env seed.
        _rotated_refresh = (new_refresh or "").strip() or refresh
        if _rotated_refresh:
            _persist_refresh(_rotated_refresh)
        _cache = (token.strip(), time.monotonic() + expires_in)
        return _cache[0]


def _persist_refresh(token: str) -> None:
    temporary: str | None = None
    try:
        path = refresh_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # mkstemp creates mode 0600, including when replacing a legacy
            # world-readable file. replace is atomic: no partial token reads.
            json.dump({"context": _context_key, "refresh_token": token}, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary, path)
    except OSError:
        pass  # _rotated_refresh still contains the only valid next token
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass


def reset_cache() -> None:
    """Clear in-memory session state (restart simulation / tests)."""
    global _cache, _context_key, _rotated_refresh, _refresh_lock, _lock_loop
    _cache = None
    _context_key = None
    _rotated_refresh = None
    _refresh_lock = None
    _lock_loop = None


async def _post(engine: HttpEngine | None, url: str, **kwargs):
    if engine is not None:
        return await engine.request("POST", url, retries=1, **kwargs)
    # Refresh tokens are single use: do not replay failed POSTs automatically.
    async with httpx.AsyncClient(timeout=10.0) as client:
        return await client.post(url, **kwargs)


async def searches_remaining(engine: HttpEngine | None = None) -> int | None:
    """Best-effort account quota; unknown/auth/network failures return None."""
    try:
        token = await access_token(engine)
        resp = await _post(
            engine,
            f"{(env(API2_URL_ENV) or API2_URL).rstrip('/')}/trpc/user.whoami?batch=1",
            # api2 uses access-token, not Supabase's Authorization header.
            headers={"access-token": token, "Content-Type": "application/json"},
            json={},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        for row in data if isinstance(data, list) else [data]:
            result = row.get("result") if isinstance(row, dict) else None
            inner = result.get("data") if isinstance(result, dict) else None
            value = inner.get("maxSearchesRemaining") if isinstance(inner, dict) else None
            if type(value) is int and value >= 0:
                return value
    except Exception:  # noqa: BLE001 — quota is informational only
        pass
    return None
