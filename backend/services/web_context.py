"""Web-context enrichment via the FlyBasis Search MCP connector.

THIS IS NOT AWARD DATA.  Nothing in this module touches award availability,
pricing or seat counts, and nothing it returns may ever be merged into the
search results.  It calls the three read-only tools of the connector in
``flybasis-mcp/`` — ``web_search``, ``instant_answer`` and ``fetch_url`` — over
the MCP streamable-HTTP transport, and hands back general web data (SERP
snippets, DuckDuckGo instant answers, cleaned page text) as clearly-labelled
supplementary context.

Award availability still comes exclusively from the providers in
``services.aggregator`` (Flybasis, the award-flight Socket.IO feed at
``enterprise-api.flybasis.com``).  That service and this connector are separate
deployments; see ``flybasis-mcp/README.md``.

Every payload returned by this module carries ``kind="web_context"``,
``is_award_data=False`` and a human-readable ``disclaimer`` so no caller — and
no UI — can mistake it for search output.  No function here raises: a failing
connector returns ``ok=False`` with a reason, and the UI simply hides the panel.
"""
from __future__ import annotations

import json
import os
import time
from typing import Any

from core.http_engine import HttpEngine, ProviderError
from services import agentsearch

# ------------------------------------------------------------------ config --

# Either connector speaks the same 3-tool MCP surface, so either URL works:
#   AGENTSEARCH_MCP_URL -> https://agentsearch-mcp.vercel.app/mcp
#   FLYBASIS_MCP_URL    -> https://flybasis-mcp.vercel.app/mcp (keyless)
AGENTSEARCH_MCP_URL_ENV = "AGENTSEARCH_MCP_URL"
MCP_URL_ENV = "FLYBASIS_MCP_URL"
TIMEOUT_ENV = "FLYBASIS_MCP_TIMEOUT"
DEFAULT_MCP_URL = "https://flybasis-mcp.vercel.app/mcp"
DEFAULT_TIMEOUT = 12.0

# MCP protocol version accepted by the connector (see flybasis-mcp/test/smoke.mjs).
PROTOCOL_VERSION = "2025-06-18"
CLIENT_INFO = {"name": "spicytool-web-context", "version": "1.0.0"}

# The tools this module is allowed to call. All three are annotated read-only
# upstream; we never invent a tool name the connector does not publish.
TOOLS = ("web_search", "instant_answer", "fetch_url")

DISCLAIMER = (
    "Web context from the FlyBasis Search MCP connector. This is general web "
    "data — search results, instant answers and page text — NOT award "
    "availability, pricing or seat counts. It never contributes to search "
    "results."
)

AGENTSEARCH_DISCLAIMER = (
    "Web context from the AgentSearch web-search API (RapidAPI). This is "
    "general web data — search results and page snippets — NOT award "
    "availability, pricing or seat counts. It never contributes to search "
    "results."
)

# Web search is slower than an award lookup, so this module keeps its own
# pooled client instead of borrowing the 3.5s provider engine.
# Cached per running event loop — a pool bound to a closed loop raises
# "Event loop is closed" on its next request (see services/agentsearch.py).
_engine: HttpEngine | None = None
_engine_loop: object | None = None


def mcp_url() -> str:
    """Endpoint of the FlyBasis Search MCP connector."""
    return (
        os.environ.get(AGENTSEARCH_MCP_URL_ENV)
        or os.environ.get(MCP_URL_ENV)
        or DEFAULT_MCP_URL
    ).strip().rstrip("/")


def timeout() -> float:
    raw = (os.environ.get(TIMEOUT_ENV) or "").strip()
    if raw:
        try:
            return max(1.0, float(raw))
        except ValueError:
            pass
    return DEFAULT_TIMEOUT


def _get_engine() -> HttpEngine:
    global _engine, _engine_loop
    import asyncio

    try:
        loop: object | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if _engine is None or _engine_loop is not loop:
        _engine = HttpEngine(timeout=timeout())
        _engine_loop = loop
    return _engine


# ------------------------------------------------------------- transport ----

def _parse_response(resp) -> dict:
    """Parse a JSON-RPC reply.

    StreamableHTTPServerTransport answers with either a plain JSON body or an
    SSE stream (``event: message\\ndata: {...}\\n\\n``) depending on the client's
    Accept header and SDK version. The connector's own smoke test handles both;
    so do we.
    """
    text = resp.text or ""
    ctype = resp.headers.get("content-type", "")
    looks_sse = (
        "text/event-stream" in ctype
        or text.lstrip().startswith("event:")
        or text.lstrip().startswith("data:")
        or "\ndata:" in text
    )
    if looks_sse:
        line = next(
            (ln for ln in text.splitlines() if ln.startswith("data:")), None
        )
        if line is None:
            raise ProviderError("MCP connector returned an SSE stream with no data line")
        return json.loads(line[len("data:"):].strip())
    if not text.strip():
        raise ProviderError("MCP connector returned an empty body")
    return json.loads(text)


async def _rpc(method: str, params: dict, req_id: int) -> dict:
    """One stateless JSON-RPC POST to the connector's /mcp endpoint."""
    body = {"jsonrpc": "2.0", "id": req_id, "method": method, "params": params}
    headers = {
        "content-type": "application/json",
        # The connector's own smoke test sends both; the SDK picks its shape.
        "accept": "application/json, text/event-stream",
    }
    # retries=1: a web lookup is best-effort enrichment, never worth a backoff.
    resp = await _get_engine().request(
        "POST", mcp_url(), headers=headers, json=body, retries=1
    )
    resp.raise_for_status()
    return _parse_response(resp)


def _unwrap_tool_result(rpc: dict) -> Any:
    """Pull the tool's payload out of a tools/call result envelope."""
    if err := rpc.get("error"):
        raise ProviderError(f"MCP error: {err.get('message') or err}")
    result = rpc.get("result") or {}
    content = result.get("content") or []
    text = next(
        (c.get("text") for c in content if c.get("type") == "text" and c.get("text")),
        None,
    )
    if result.get("isError"):
        raise ProviderError(text or "MCP tool returned an error")
    if text is None:
        raise ProviderError("MCP tool returned no text content")
    # The connector forwards upstream JSON verbatim as a JSON string.
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return {"text": text}


# ---------------------------------------------------------------- public ----

def _payload(
    tool: str,
    ok: bool,
    *,
    data: Any = None,
    error: str | None = None,
    source: str | None = None,
    endpoint: str | None = None,
) -> dict:
    """Wrap anything this module returns so it can never be read as award data."""
    return {
        "kind": "web_context",
        "is_award_data": False,
        "source": source or "flybasis-mcp",
        "endpoint": endpoint or mcp_url(),
        "tool": tool,
        "disclaimer": (
            AGENTSEARCH_DISCLAIMER if source == "agentsearch" else DISCLAIMER
        ),
        "ok": ok,
        "error": error,
        "data": data,
    }


def backend_name() -> str:
    """Which web backend serves the context panel right now."""
    return "agentsearch" if agentsearch.configured() else "flybasis-mcp"


async def _agentsearch_tool(tool: str, arguments: dict) -> dict | None:
    """Serve a tool from AgentSearch (RapidAPI) when a key is configured.

    Returns None when AgentSearch cannot serve this tool, so the caller falls
    back to the keyless MCP connector. Never raises.
    """
    if not agentsearch.configured():
        return None
    endpoint = f"{agentsearch.base_url()}" + {
        "web_search": agentsearch.SEARCH_PATH,
        "instant_answer": agentsearch.ANSWER_PATH,
        "fetch_url": agentsearch.FETCH_PATH,
    }.get(tool, agentsearch.SEARCH_PATH)
    try:
        if tool == "web_search":
            data = await agentsearch.search(
                str(arguments.get("q") or ""), int(arguments.get("limit") or 5)
            )
        elif tool == "instant_answer":
            data = await agentsearch.instant_answer(str(arguments.get("q") or ""))
        elif tool == "fetch_url":
            data = await agentsearch.fetch_url(
                str(arguments.get("url") or ""),
                str(arguments.get("format") or "text"),
                int(arguments.get("maxChars") or 20000),
            )
        else:
            return None
        return _payload(
            tool, True, data=data, source="agentsearch", endpoint=endpoint
        )
    except Exception as exc:  # noqa: BLE001 — fall through to the MCP connector
        return _payload(
            tool,
            False,
            error=f"{exc.__class__.__name__}: {exc}",
            source="agentsearch",
            endpoint=endpoint,
        )


async def call_tool(tool: str, arguments: dict) -> dict:
    """Call one connector tool and return it labelled as non-award context.

    Never raises.
    """
    if tool not in TOOLS:
        return _payload(tool, False, error=f"Unknown tool '{tool}' (expected one of {', '.join(TOOLS)})")
    # Preferred backend: AgentSearch (RapidAPI) when an operator supplied a key.
    primary = await _agentsearch_tool(tool, arguments)
    if primary is not None and primary["ok"]:
        return primary
    try:
        await _rpc(
            "initialize",
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": CLIENT_INFO,
            },
            1,
        )
        rpc = await _rpc("tools/call", {"name": tool, "arguments": arguments}, 2)
        return _payload(tool, True, data=_unwrap_tool_result(rpc))
    except Exception as exc:  # noqa: BLE001 — enrichment must never break a request
        if primary is not None:
            # Both backends failed: report both so the operator can fix the key.
            primary["error"] = (
                f"AgentSearch: {primary['error']} | "
                f"MCP fallback: {exc.__class__.__name__}: {exc}"
            )
            return primary
        return _payload(tool, False, error=f"{exc.__class__.__name__}: {exc}")


async def web_search(q: str, limit: int = 5) -> dict:
    return await call_tool("web_search", {"q": q, "limit": max(1, min(20, limit))})


async def instant_answer(q: str) -> dict:
    return await call_tool("instant_answer", {"q": q})


async def fetch_url(url: str, fmt: str = "text", max_chars: int = 20000) -> dict:
    return await call_tool(
        "fetch_url",
        {"url": url, "format": fmt, "maxChars": max(500, min(500000, max_chars))},
    )


def build_query(
    origin: str | None,
    destination: str | None,
    program_name: str | None = None,
    cabin: str | None = None,
) -> str | None:
    """Compose a route/programme query for web context.

    Deliberately asks for background and transfer-bank discussion, never for
    prices — the connector has no award data and we do not want a snippet
    reading like availability.
    """
    if not origin or not destination:
        return None
    parts = [f"{origin.upper()} to {destination.upper()}"]
    if program_name:
        parts.append(program_name)
    if cabin:
        parts.append(f"{cabin} class")
    parts.append("award booking transfer partners guide")
    return " ".join(parts)


async def route_context(
    origin: str | None,
    destination: str | None,
    program_name: str | None = None,
    cabin: str | None = None,
    limit: int = 5,
) -> dict:
    """Web context for a route, labelled as non-award data."""
    q = build_query(origin, destination, program_name, cabin)
    if not q:
        return _payload("web_search", False, error="origin and destination are required")
    t0 = time.monotonic()
    out = await web_search(q, limit=limit)
    out["query"] = q
    out["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
    return out


async def status() -> dict:
    """Which backend serves web context, and whether it is configured."""
    return {
        "kind": "web_context",
        "is_award_data": False,
        "backend": backend_name(),
        "disclaimer": (
            AGENTSEARCH_DISCLAIMER if backend_name() == "agentsearch" else DISCLAIMER
        ),
        "agentsearch": await agentsearch.status(),
        "mcp": {"endpoint": mcp_url(), "timeout": timeout()},
        "tools": list(TOOLS),
    }


async def aclose() -> None:
    global _engine, _engine_loop
    if _engine is not None:
        await _engine.aclose()
        _engine = None
        _engine_loop = None
    await agentsearch.aclose()
