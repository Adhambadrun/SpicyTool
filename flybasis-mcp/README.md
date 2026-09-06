# agentsearch-mcp

A remote **MCP (Model Context Protocol)** connector for [AgentSearch API](https://agentsearch-api.vercel.app) — an LLM/MCP-native web toolkit for agents and RAG pipelines: **web search** (provider-abstracted SERP), **keyless instant answers**, and **URL → clean text/markdown** fetch ready for an LLM context window.

**Live:** `https://agentsearch-mcp.vercel.app/mcp` — 3 tools, one per AgentSearch `/v1` endpoint. Since the upstream AgentSearch deployment is metered (RapidAPI/Apify) and gates `/v1/*` behind a RapidAPI proxy-secret guard, this connector authenticates its own outbound calls with that same secret (`AGENTSEARCH_MCP_PROXY_SECRET`, sent as the `X-RapidAPI-Proxy-Secret` header) and applies a soft per-IP rate limit (`AGENTSEARCH_MCP_RATE_LIMIT`, default 30 tool-calls/hour, in-memory) so the free MCP tier stays a discovery channel rather than an unmetered bypass of the paid listing — see `lib/ratelimit.js`.

## What this is, and why it's a separate connector

AgentSearch API is a plain REST API. Any HTTP client can already call it directly. This repo exists because **MCP clients (Claude, ChatGPT, and other MCP-aware agents) don't consume arbitrary REST APIs — they consume MCP tools.** `agentsearch-mcp` is a thin adapter layer that:

- Exposes each AgentSearch endpoint as a discoverable, typed MCP **tool** (name, description, zod input schema, annotations) that an LLM can reason about and call directly, instead of having to be taught the REST surface out-of-band.
- Speaks the MCP **streamable-HTTP** transport at a single `/mcp` endpoint, so it can be registered as a connector in Claude, ChatGPT, or any other MCP client with one URL.
- Does nothing else. It has no business logic of its own — every tool call is a pass-through `fetch` to AgentSearch, and the JSON response AgentSearch returns is handed back verbatim as the tool result.

## Free discovery tier over a metered API

This connector is a **free discovery/growth tier** in front of the metered AgentSearch API. The upstream (`agentsearch-api.vercel.app`) is sold on RapidAPI/Apify; this MCP wrapper authenticates to it with the shared RapidAPI proxy secret and caps usage per-IP so it stays a taste-test rather than an unmetered path around the paid plans. Heavy/production volume should go through [AgentSearch on RapidAPI/Apify](https://agentsearch-api.vercel.app).

## Authentication: None on the MCP side (deliberate)

AgentSearch's data has no per-user dimension — it's public web data (SERP results, DuckDuckGo instant answers, cleaned page text). There is nothing to gate per-caller, so this connector intentionally ships with:

- No OAuth, no login, no bearer tokens for the MCP caller
- No Supabase / database
- No demo-vs-real account split — every caller gets the same real, live data

`api/mcp.js` builds a fresh, stateless `McpServer` per request and serves it with zero MCP-caller auth checks. The only outbound auth is the upstream RapidAPI proxy secret described above, which the connector holds server-side.

## Tool list

One tool per AgentSearch `/v1` endpoint (from `agentsearch-api/openapi.yaml`; `/api/health` is intentionally not wrapped):

| Tool | AgentSearch endpoint | Description |
|---|---|---|
| `web_search` | `GET /v1/search` | Web SERP via a provider-abstracted backend (Brave or Serper). Returns normalized results with position/title/url/snippet/domain. |
| `instant_answer` | `GET /v1/answer` | Keyless DuckDuckGo instant answer — definitions, entities, quick facts. |
| `fetch_url` | `GET /v1/fetch` | Keyless, SSRF-guarded URL → clean, boilerplate-free text or markdown for RAG. |

All 3 tools are read-only and annotated `{ readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true }` — none of them write anything, and all of them reflect live, externally-changing web data.

## How it wraps agentsearch-api

Each tool handler does a plain `fetch(\`${AGENTSEARCH_MCP_API_BASE_URL}${path}\`, ...)` against the real AgentSearch REST API and returns the parsed JSON as MCP tool-result content:

```js
{ content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
```

The outbound request carries `X-RapidAPI-Proxy-Secret: <AGENTSEARCH_MCP_PROXY_SECRET>` so it passes the upstream guard on `/v1/*`. Upstream HTTP errors (4xx/5xx) are caught and surfaced as a typed MCP error result via an `asError` helper rather than crashing the request.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `AGENTSEARCH_MCP_API_BASE_URL` | `https://agentsearch-api.vercel.app` | Upstream AgentSearch base URL (override for local/self-hosted testing). |
| `AGENTSEARCH_MCP_PROXY_SECRET` | _(empty)_ | RapidAPI proxy secret, sent as `X-RapidAPI-Proxy-Secret` to pass the upstream `/v1/*` guard. Without it, guarded routes return 403. |
| `AGENTSEARCH_MCP_RATE_LIMIT` | `30` | Soft per-IP `tools/call` cap per hour (in-memory, per serverless instance). |

## Project layout

```
api/mcp.js       MCP endpoint (StreamableHTTPServerTransport, stateless, no MCP-caller auth)
api/health.js    GET /api/health
lib/tools.js     All 3 tool definitions (zod schemas + fetch-and-forward handlers, proxy-secret auth)
lib/ratelimit.js Soft per-IP tools/call rate limiter
local-server.js  Plain-Node http server for local dev / smoke testing (not deployed)
test/smoke.mjs   Real end-to-end smoke test (initialize, tools/list, tools/call)
vercel.json      Routes /mcp -> api/mcp.js, /health -> api/health.js
server.json      MCP registry manifest
```

## Local development

```bash
npm install
npm run dev          # starts local-server.js on :3900
npm run smoke        # runs test/smoke.mjs against the local server
```

To exercise a real end-to-end fetch through the upstream guard, set the proxy secret first:

```bash
export AGENTSEARCH_MCP_PROXY_SECRET=<the RapidAPI proxy secret>
npm run dev &
npm run smoke
```
