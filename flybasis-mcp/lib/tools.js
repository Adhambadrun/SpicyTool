// lib/tools.js — MCP tool definitions for the FlyBasis Search connector.
//
// One tool per FlyBasis Search API endpoint (see openapi.yaml in the flybasis-search-api
// repo for the source of truth). Every tool is a thin, stateless fetch against
// the live FlyBasis Search deployment — no caching, no per-caller auth, no per-caller
// state. FlyBasis Search's data (web SERP results, DuckDuckGo instant answers, cleaned
// URL text/markdown) is fully public, so there is nothing to gate on the
// MCP-caller side: every caller sends the same request shape and gets the same
// real data back.
//
// The upstream flybasis-search-api deployment IS metered (sold on RapidAPI/Apify) and
// its /v1/* routes sit behind a RapidAPI proxy-secret guard, so this connector
// authenticates its own outbound calls with that same proxy secret
// (FLYBASIS_MCP_PROXY_SECRET, sent as the X-RapidAPI-Proxy-Secret header) and
// applies its own soft per-IP rate limit (api/mcp.js + lib/ratelimit.js) so this
// free MCP tier stays a discovery/growth channel rather than an unmetered bypass
// of the paid listing.
//
// Base URL is configurable via FLYBASIS_MCP_API_BASE_URL for local/self-hosted
// testing; it defaults to the production deployment.

import { z } from 'zod';

const BASE_URL = (process.env.FLYBASIS_MCP_API_BASE_URL || process.env.AGENTSEARCH_MCP_API_BASE_URL || 'https://agentsearch-api.vercel.app').replace(/\/+$/, '');
// The production FlyBasis Search deployment gates /v1/* behind a RapidAPI-proxy-secret
// (see flybasis-search-api — only /api/health is open on the origin). This connector
// authenticates as that same RapidAPI-proxy consumer class to reach real data by
// sending the secret in the X-RapidAPI-Proxy-Secret header — see lib/ratelimit.js
// for the usage cap that keeps this a free/discovery tier rather than an unmetered
// bypass of the paid RapidAPI/Apify listing.
const PROXY_SECRET = process.env.FLYBASIS_MCP_PROXY_SECRET || process.env.AGENTSEARCH_MCP_PROXY_SECRET || '';

const asText = (obj) => ({ content: [{ type: 'text', text: JSON.stringify(obj, null, 2) }] });
const asError = (err) => ({
  isError: true,
  content: [{ type: 'text', text: `Error: ${err.message || String(err)}` }],
});

// Every FlyBasis Search tool only reads public web data — never writes, never touches
// a caller's account (there is no account). openWorldHint is true because the
// underlying data comes from the live web (an open, changing world outside this
// server's control).
const RO = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };

function buildUrl(path, params = {}) {
  const url = new URL(BASE_URL + path);
  for (const [key, value] of Object.entries(params)) {
    if (value === undefined || value === null || value === '') continue;
    url.searchParams.set(key, String(value));
  }
  return url;
}

async function callUpstream(path, params) {
  const url = buildUrl(path, params);
  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 25_000);
  let res;
  try {
    const headers = { accept: 'application/json' };
    if (PROXY_SECRET) headers['X-RapidAPI-Proxy-Secret'] = PROXY_SECRET;
    res = await fetch(url, { headers, signal: controller.signal });
  } catch (e) {
    throw new Error(`FlyBasis Search API request failed (${url.pathname}${url.search}): ${e.message || e}`);
  } finally {
    clearTimeout(timeout);
  }

  const text = await res.text();
  let body;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = { raw: text };
  }

  if (!res.ok) {
    const message = body?.error?.message || `FlyBasis Search API returned HTTP ${res.status} for ${url.pathname}${url.search}`;
    const err = new Error(message);
    err.status = res.status;
    err.body = body;
    throw err;
  }
  return body;
}

// Wraps a (args) => { path, params } mapper into an MCP tool handler that calls
// FlyBasis Search and returns the JSON as tool-result content, or a typed error.
function forward(mapper) {
  return async (args = {}) => {
    try {
      const { path, params } = mapper(args);
      return asText(await callUpstream(path, params));
    } catch (err) {
      return asError(err);
    }
  };
}

export function registerTools(server) {
  server.registerTool(
    'web_search',
    {
      title: 'Web search (SERP)',
      description:
        'Search the web via a provider-abstracted backend (Brave or Serper, configured operator-side). Returns normalized results, each with position, title, url, snippet, source, and domain, plus a meta envelope (cache state, provider, took_ms). Returns a provider_required error if no search provider key is configured upstream. Use this for a full web SERP; use instant_answer for quick facts/definitions.',
      inputSchema: {
        q: z.string().describe('Search query.'),
        provider: z.enum(['brave', 'serper']).optional().describe('Force a specific provider. Omit to use whichever is configured operator-side.'),
        limit: z.number().int().min(1).max(20).optional().describe('Max results, clamped to 1-20. Default 10.'),
        country: z.string().optional().describe('ISO-3166 alpha-2 region, lowercase. Default us.'),
      },
      annotations: RO,
    },
    forward(({ q, provider, limit, country }) => ({
      path: '/v1/search',
      params: { q, provider, limit, country },
    }))
  );

  server.registerTool(
    'instant_answer',
    {
      title: 'Instant answer (keyless)',
      description:
        'DuckDuckGo Instant Answer API — definitions, entities, and quick facts. Returns a normalized answer with heading, type (abstract/answer/disambiguation), source, sourceUrl, optional image/definition, and relatedTopics, plus a meta envelope. Not a full web SERP; use web_search for that. Works with no API key.',
      inputSchema: {
        q: z.string().describe('Query.'),
      },
      annotations: RO,
    },
    forward(({ q }) => ({
      path: '/v1/answer',
      params: { q },
    }))
  );

  server.registerTool(
    'fetch_url',
    {
      title: 'Fetch a URL as clean text/markdown (RAG-ready)',
      description:
        'Fetches any public http(s) URL, strips boilerplate (scripts, nav, footer, ads), and returns clean text or markdown ready for an LLM context window. SSRF-guarded — refuses private/loopback/internal hosts. Returns url, title, format, length, content (and finalUrl when redirected, plus up to 50 extracted links when links=true), with a meta envelope. Works with no API key.',
      inputSchema: {
        url: z.string().describe('The URL to fetch (public http/https only).'),
        format: z.enum(['text', 'markdown']).optional().describe('Output format. Default text.'),
        maxChars: z.number().int().min(500).max(500000).optional().describe('Max characters of content returned, clamped 500-500000. Default 100000.'),
        links: z.boolean().optional().describe('Also return up to 50 extracted links. Default false.'),
      },
      annotations: RO,
    },
    forward(({ url, format, maxChars, links }) => ({
      path: '/v1/fetch',
      params: { url, format, maxChars, links },
    }))
  );
}

export const BASE_URL_FOR_HEALTH = BASE_URL;
