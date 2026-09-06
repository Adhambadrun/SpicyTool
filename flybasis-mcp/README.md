# FlyBasis MCP connector (keyless, self-contained)

This connector lives in the SpicyTool repository under `flybasis-mcp/` and is a
**self-contained, keyless** MCP (Model Context Protocol) web toolkit. Everything
it needs to run is in this repo file — no external API keys, no RapidAPI, no
paid proxy, no separate upstream deployment.

**Live (needs redeploy — see below):** `https://flybasis-mcp.vercel.app/mcp` —
3 tools, one per capability.

> ⚠️ **This is NOT award data.** Award availability in SpicyTool comes **only**
> from `backend/providers/flybasis.py`, which dials the Flybasis Socket.IO award
> feed (`https://enterprise-api.flybasis.com/sockets/v1/stream-flights`,
> `auth={"token": <FLYBASIS_API_KEY>}`). This connector supplies general
> **web context** (`web_search` / `instant_answer` / `fetch_url`) for the
> itinerary "Web context" panel. It will never produce flights.

## What changed (v2.0.0 — why this exists)

The previous version passed every tool call through to a metered upstream
(`agentsearch-api.vercel.app`) that gates `/v1/*` behind a RapidAPI proxy
secret. That secret was not in the repo (and should never be) — so the connector
depended on a deployment that no longer exists (`DEPLOYMENT_NOT_FOUND`) plus a
secret only the operator could hold. It is now rebuilt so **nothing is required
from outside this file**:

| Tool | v2.0.0 implementation | Keys |
|---|---|---|
| `web_search` | DuckDuckGo Lite organic SERP, with automatic Wikipedia OpenSearch fallback | none |
| `instant_answer` | DuckDuckGo Instant Answer API, Wikipedia REST summary fallback | none |
| `fetch_url` | Self-hosted fetch + local SSRF guard + boilerplate stripping → text/markdown | none |

Optional upgrades (still no code change): set `FLYBASIS_MCP_BRAVE_KEY` or
`FLYBASIS_MCP_SERPER_KEY` to use a commercial SERP provider; the connector keeps
the same tools/schema either way.

## Tool list

| Tool | Description |
|---|---|
| `web_search` | Normalized organic results: position, title, url, snippet, source, domain + meta envelope (provider, sources, took_ms, ads_filtered). |
| `instant_answer` | Keyless instant answer: heading, type (abstract/answer/disambiguation), text, source, sourceUrl, optional image, related topics + meta. |
| `fetch_url` | SSRF-guarded public URL → clean text (default) or markdown, plus `finalUrl` and up to 50 extracted links when `links=true`. |

All 3 tools are read-only, annotated `{ readOnlyHint: true, destructiveHint:
false, idempotentHint: true, openWorldHint: true }`, return upstream JSON
verbatim in the tool result, and surface failures as typed MCP errors (never a
crashed request).

## Environment variables

Nothing is required. Defaults are keyless.

| Var | Default | Purpose |
|---|---|---|
| `FLYBASIS_MCP_RATE_LIMIT` | `30` | Soft per-IP `tools/call` cap per hour (in-memory, per instance). |
| `FLYBASIS_MCP_BRAVE_KEY` | _(empty)_ | Optional: use Brave Search API for `web_search`. |
| `FLYBASIS_MCP_SERPER_KEY` | _(empty)_ | Optional: use Serper (Google) for `web_search`. |
| `FLYBASIS_MCP_API_BASE_URL` | _(empty)_ | Legacy compat only: forward to a custom upstream REST API. |
| `FLYBASIS_MCP_PROXY_SECRET` | _(empty)_ | Legacy compat only: header sent when forwarding upstream. |
| `FLYBASIS_MCP_FORCE_LEGACY` | `0` | Legacy compat only: force pass-through mode. |

## Project layout

```
api/mcp.js       MCP endpoint (StreamableHTTPServerTransport, stateless, no MCP-caller auth)
api/health.js    GET /api/health (reports mode + configured providers)
lib/tools.js     All 3 tools: keyless implementations + parsers + SSRF guard
lib/ratelimit.js Soft per-IP tools/call rate limiter
local-server.js  Plain-Node http server for local dev / smoke testing (not deployed)
test/smoke.mjs   Real end-to-end smoke test (initialize, tools/list, tools/call)
test/offline.mjs ZERO-network unit tests for parsers/normalizers (runs anywhere)
vercel.json      Routes /mcp -> api/mcp.js, /health -> api/health.js
server.json      MCP registry manifest
```

## Local development

```bash
npm install
npm run dev          # starts local-server.js on :3900
npm run test:offline # parser unit tests — NO network needed
npm run smoke        # protocol smoke test (data calls need outbound network)
```

## Deploy (one command — no keys, no extra services)

The previous `flybasis-mcp.vercel.app` deployment is gone (`DEPLOYMENT_NOT_FOUND`).
Re-deploy from this folder to any Vercel project with:

```bash
cd flybasis-mcp
npx vercel --prod     # or: import this folder into https://vercel.com/new
```

That serves `https://<your-project>.vercel.app/mcp`. Point SpicyTool at it with
`FLYBASIS_MCP_URL=https://<your-project>.vercel.app/mcp` in `.env`. Any
Node≥20 serverless host works the same way (the handlers use only `fetch`, the
MCP SDK, and `node:dns/promises`). No databases, no auth infra, no secrets.

## Relationship to the award feed

Do not confuse the two:

- **Award search (the actual product):** `backend/providers/flybasis.py` +
  `Flybasis-index.md` — Flybasis WebSocket award feed, gated by
  `FLYBASIS_API_KEY` issued directly by Flybasis to the operator. **The repo
  deliberately ships without that key** (credential-gating is a hard project
  constraint); see `../FLYBASIS_GO_LIVE.md` for exactly how to connect it.
- **This connector:** general web context for RAG/enrichment. Keyless, no award
  data, never merged into search results (`backend/services/web_context.py`
  labels everything `is_award_data=false`).
