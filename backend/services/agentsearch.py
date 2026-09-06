"""AgentSearch (RapidAPI) web-search backend.

THIS IS NOT AWARD DATA. AgentSearch is a general web-search relay
(`https://agentsearch.p.rapidapi.com/v1/search`) that proxies Brave / Google /
DuckDuckGo SERPs. It returns web pages, never award availability, pricing or
seat counts. Award availability still comes exclusively from the providers in
``services.aggregator`` (the Flybasis Socket.IO award feed).

This module is the *first-choice* backend for the "Web context" panel: when
``AGENTSEARCH_API_KEY`` is set the app calls RapidAPI directly instead of the
keyless MCP connector, which is slower and rate-limited. It normalizes every
upstream response shape AgentSearch is known to emit onto the same envelope the
MCP connector produces, so ``services.web_context`` and the UI need no special
cases:

    {"results": [{"position", "title", "url", "snippet", "source", "domain"}],
     "meta": {"provider", "took_ms", "count", "query"}}

Credential resolution (in order):
  1. ``AGENTSEARCH_API_KEY``
  2. ``FLYBASIS_API_KEY`` — only when it is shaped like a RapidAPI key
     (``...msh...jsn...``). Operators frequently paste their RapidAPI key into
     the Flybasis slot; rather than silently failing we honour it *for web
     context only*. It is never sent to the Flybasis award socket.

No function here raises: failures return ``ok=False`` with a reason.
"""
from __future__ import annotations

import os
import re
import time
from typing import Any
from urllib.parse import urlparse

from core.http_engine import HttpEngine, ProviderError

# ------------------------------------------------------------------ config --

KEY_ENV = "AGENTSEARCH_API_KEY"
HOST_ENV = "AGENTSEARCH_HOST"
PROVIDER_ENV = "AGENTSEARCH_PROVIDER"
COUNTRY_ENV = "AGENTSEARCH_COUNTRY"
TIMEOUT_ENV = "AGENTSEARCH_TIMEOUT"

DEFAULT_HOST = "agentsearch.p.rapidapi.com"
DEFAULT_PROVIDER = "brave"
DEFAULT_COUNTRY = "us"
DEFAULT_TIMEOUT = 12.0

# The three documented AgentSearch /v1 endpoints (agentsearch-api/openapi.yaml).
# /api/health is deliberately not wrapped.
SEARCH_PATH = "/v1/search"
ANSWER_PATH = "/v1/answer"
FETCH_PATH = "/v1/fetch"

# A RapidAPI key looks like: <20 hex>msh<hex>p<hex>jsn<hex>
_RAPIDAPI_KEY_RE = re.compile(r"^[A-Za-z0-9]{10,}msh[A-Za-z0-9]+jsn[A-Za-z0-9]+$")

_engine: HttpEngine | None = None


def looks_like_rapidapi_key(value: str | None) -> bool:
    """True when a credential is shaped like a RapidAPI application key."""
    return bool(value) and bool(_RAPIDAPI_KEY_RE.match(value.strip()))


def api_key() -> str | None:
    """The AgentSearch credential, or None when the backend is not configured."""
    direct = (os.environ.get(KEY_ENV) or "").strip()
    if direct:
        return direct
    # Operator pasted a RapidAPI key into the Flybasis slot: use it here (web
    # context only). A real Flybasis socket token is never RapidAPI-shaped.
    fallback = (os.environ.get("FLYBASIS_API_KEY") or "").strip()
    if looks_like_rapidapi_key(fallback):
        return fallback
    return None


def configured() -> bool:
    return api_key() is not None


def host() -> str:
    return (os.environ.get(HOST_ENV) or DEFAULT_HOST).strip().strip("/")


def base_url() -> str:
    return f"https://{host()}"


def provider() -> str:
    return (os.environ.get(PROVIDER_ENV) or DEFAULT_PROVIDER).strip() or DEFAULT_PROVIDER


def country() -> str:
    return (os.environ.get(COUNTRY_ENV) or DEFAULT_COUNTRY).strip() or DEFAULT_COUNTRY


def timeout() -> float:
    raw = (os.environ.get(TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_TIMEOUT


def _get_engine() -> HttpEngine:
    global _engine
    if _engine is None:
        _engine = HttpEngine(timeout=timeout())
    return _engine


def headers() -> dict[str, str]:
    key = api_key() or ""
    return {
        "accept": "application/json",
        "content-type": "application/json",
        "x-rapidapi-host": host(),
        "x-rapidapi-key": key,
    }


# ------------------------------------------------------------ normalization --

# AgentSearch (and the SERP providers behind it) use different field names per
# provider; accept every spelling seen in the wild instead of guessing one.
_TITLE_KEYS = ("title", "name", "heading", "header")
_URL_KEYS = ("url", "link", "href", "displayUrl", "display_url", "displayed_link")
_SNIPPET_KEYS = (
    "snippet", "description", "desc", "text", "content", "summary", "abstract", "body",
)
_LIST_KEYS = (
    "results", "web", "organic", "organic_results", "items", "data", "documents", "hits",
)


def _first(d: dict, keys: tuple[str, ...]) -> str:
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
        if isinstance(v, dict):
            # Brave nests {"description": {"value": ...}} in some shapes.
            inner = v.get("value") or v.get("text")
            if isinstance(inner, str) and inner.strip():
                return inner.strip()
    return ""


def domain_of(url: str) -> str:
    try:
        netloc = urlparse(url).netloc.lower()
    except ValueError:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def _extract_rows(payload: Any) -> list[dict]:
    """Find the organic-result list anywhere in an AgentSearch response."""
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if not isinstance(payload, dict):
        return []
    for key in _LIST_KEYS:
        value = payload.get(key)
        if isinstance(value, list) and any(isinstance(r, dict) for r in value):
            return [r for r in value if isinstance(r, dict)]
        # Brave-style: {"web": {"results": [...]}}
        if isinstance(value, dict):
            nested = _extract_rows(value)
            if nested:
                return nested
    return []


def normalize_search(payload: Any, query: str, took_ms: int = 0) -> dict:
    """Map any AgentSearch/SERP payload onto the connector's result envelope.

    Pure function of the wire payload — fully testable offline.
    """
    rows = _extract_rows(payload)
    results: list[dict] = []
    for i, row in enumerate(rows):
        url = _first(row, _URL_KEYS)
        title = _first(row, _TITLE_KEYS)
        if not url and not title:
            continue
        results.append(
            {
                "position": int(row.get("position") or row.get("rank") or (i + 1)),
                "title": title or url,
                "url": url,
                "snippet": _first(row, _SNIPPET_KEYS),
                "source": str(row.get("source") or "").strip() or domain_of(url),
                "domain": str(row.get("domain") or "").strip() or domain_of(url),
                "published": row.get("published") or None,
            }
        )
    # Documented success envelope:
    # meta{cached, stale, as_of, source, provider, took_ms}, query, count, results.
    meta_in = payload.get("meta") if isinstance(payload, dict) else None
    meta_in = meta_in if isinstance(meta_in, dict) else {}
    upstream = str(meta_in.get("provider") or "")
    upstream_query = payload.get("query") if isinstance(payload, dict) else None
    return {
        "query": upstream_query or query,
        "results": results,
        "meta": {
            "provider": upstream or f"agentsearch:{provider()}",
            "sources": [upstream or provider()],
            "count": len(results),
            # Prefer the upstream timing; fall back to our measured round-trip.
            "took_ms": meta_in.get("took_ms") if meta_in.get("took_ms") is not None else took_ms,
            "cached": bool(meta_in.get("cached", False)),
            "stale": bool(meta_in.get("stale", False)),
            "as_of": meta_in.get("as_of"),
            "ads_filtered": 0,
        },
    }


# ---------------------------------------------------------------- transport --


async def _get(path: str, params: dict) -> Any:
    key = api_key()
    if not key:
        raise ProviderError(
            f"AgentSearch is not configured — set {KEY_ENV} to your RapidAPI key."
        )
    resp = await _get_engine().request(
        "GET", f"{base_url()}{path}", headers=headers(), params=params, retries=2
    )
    if resp.status_code in (401, 403):
        raise ProviderError(
            f"AgentSearch rejected the RapidAPI key (HTTP {resp.status_code}). "
            f"Check {KEY_ENV} and that your RapidAPI account is subscribed to "
            f"{host()}."
        )
    if resp.status_code == 429:
        raise ProviderError("AgentSearch rate limit reached (HTTP 429) — try again shortly.")
    if resp.status_code >= 400:
        raise ProviderError(
            f"AgentSearch returned HTTP {resp.status_code}: {(resp.text or '')[:200]}"
        )
    try:
        return resp.json()
    except ValueError as exc:
        raise ProviderError("AgentSearch returned a non-JSON body") from exc


async def search(q: str, limit: int = 5, *, country_code: str | None = None) -> dict:
    """Raw (normalized) web search. Raises ProviderError on failure."""
    t0 = time.monotonic()
    params = {
        "query": q,
        "q": q,  # accepted by both spellings; harmless duplication
        "provider": provider(),
        "country": country_code or country(),
        "limit": max(1, min(20, int(limit))),
    }
    payload = await _get(SEARCH_PATH, params)
    return normalize_search(payload, q, int((time.monotonic() - t0) * 1000))


def normalize_answer(payload: Any, q: str) -> dict:
    """Map GET /v1/answer onto the connector's instant-answer shape."""
    if not isinstance(payload, dict):
        return {"heading": q, "type": "none", "text": "", "source": "", "sourceUrl": ""}
    # The answer body may be top-level or nested under "answer"/"data".
    body = payload
    for key in ("answer", "data", "result"):
        inner = payload.get(key)
        if isinstance(inner, dict):
            body = inner
            break
    text = _first(body, ("text", "abstract", "answer", "snippet", "description", "summary"))
    url = _first(body, ("sourceUrl", "source_url", "url", "abstractURL", "link"))
    related = []
    for r in (body.get("relatedTopics") or body.get("related_topics") or []):
        if isinstance(r, dict):
            related.append({"text": _first(r, _TITLE_KEYS + _SNIPPET_KEYS), "url": _first(r, _URL_KEYS)})
    return {
        "heading": _first(body, ("heading", "title", "name")) or q,
        "type": str(body.get("type") or ("abstract" if text else "none")),
        "text": text,
        "source": _first(body, ("source",)) or domain_of(url),
        "sourceUrl": url,
        "image": body.get("image") or None,
        "relatedTopics": related,
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
    }


async def instant_answer(q: str) -> dict:
    """Keyless DuckDuckGo instant answer via GET /v1/answer.

    Falls back to the top organic result when the upstream has no instant
    answer for the query (common for long-tail route/program phrases).
    """
    payload = await _get(ANSWER_PATH, {"query": q, "q": q})
    out = normalize_answer(payload, q)
    if out.get("text"):
        return out
    serp = await search(q, limit=3)
    top = (serp.get("results") or [None])[0]
    if not top:
        return out
    return {
        "heading": top.get("title") or q,
        "type": "abstract",
        "text": top.get("snippet") or "",
        "source": top.get("domain") or "",
        "sourceUrl": top.get("url") or "",
        "relatedTopics": [
            {"text": r.get("title"), "url": r.get("url")} for r in serp.get("results", [])[1:]
        ],
        "meta": serp.get("meta", {}),
    }


def normalize_fetch(payload: Any, url: str) -> dict:
    """Map GET /v1/fetch onto the connector's fetch_url shape."""
    if not isinstance(payload, dict):
        return {"url": url, "finalUrl": url, "text": str(payload or ""), "format": "text"}
    body = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    return {
        "url": url,
        "finalUrl": _first(body, ("finalUrl", "final_url", "url")) or url,
        "title": _first(body, _TITLE_KEYS),
        "format": str(body.get("format") or "text"),
        "text": _first(body, ("text", "content", "markdown", "body")),
        "links": body.get("links") if isinstance(body.get("links"), list) else [],
        "meta": payload.get("meta") if isinstance(payload.get("meta"), dict) else {},
    }


async def fetch_url(url: str, fmt: str = "text", max_chars: int = 20000) -> dict:
    """SSRF-guarded URL -> clean text/markdown via GET /v1/fetch."""
    payload = await _get(
        FETCH_PATH,
        {"url": url, "format": fmt, "maxChars": max(500, min(500000, int(max_chars)))},
    )
    return normalize_fetch(payload, url)


async def status() -> dict:
    """Configuration snapshot for /api/v2/providers-style diagnostics."""
    key = api_key()
    return {
        "backend": "agentsearch",
        "endpoint": f"{base_url()}{SEARCH_PATH}",
        "endpoints": {
            "web_search": f"{base_url()}{SEARCH_PATH}",
            "instant_answer": f"{base_url()}{ANSWER_PATH}",
            "fetch_url": f"{base_url()}{FETCH_PATH}",
        },
        "provider": provider(),
        "country": country(),
        "configured": bool(key),
        "key_source": (
            KEY_ENV
            if (os.environ.get(KEY_ENV) or "").strip()
            else ("FLYBASIS_API_KEY (RapidAPI-shaped)" if key else None)
        ),
        "is_award_data": False,
    }


async def aclose() -> None:
    global _engine
    if _engine is not None:
        await _engine.aclose()
        _engine = None
