// lib/tools.js — keyless, self-contained tool implementations for flybasis-mcp.
//
// This connector used to pass through to a metered upstream
// (agentsearch-api.vercel.app) that was gated behind a RapidAPI proxy secret it
// could not ship with. It has been rebuilt so EVERYTHING it needs is in this
// repo file — no external keys, no paid proxy, no third-party deployment:
//
//   web_search       DuckDuckGo Lite (organic SERP), with an automatic
//                    keyless Wikipedia OpenSearch fallback. Optional operator
//                    keys (Brave/Serper) can be supplied via env vars to get
//                    provider-abstracted SERP without changing code.
//   instant_answer   DuckDuckGo Instant Answer API (keyless JSON), with a
//                    Wikipedia REST summary fallback.
//   fetch_url        Direct server-side fetch with a local SSRF guard, then
//                    boilerplate stripping to clean text/markdown.
//
// Backward compatibility: if FLYBASIS_MCP_API_BASE_URL is pointed at a custom
// upstream (or FLYBASIS_MCP_FORCE_LEGACY=1), the old pass-through mode is
// preserved (optional FLYBASIS_MCP_PROXY_SECRET header). The default is now
// fully keyless.
//
// NOTE: this connector is GENERAL WEB CONTEXT ONLY. Award availability in
// SpicyTool comes from backend/providers/flybasis.py (the Flybasis Socket.IO
// award feed at enterprise-api.flybasis.com), NOT from this module.

import { z } from 'zod';
import { lookup as dnsLookup } from 'node:dns/promises';

// ---------------------------------------------------------------- config ---

const DEFAULT_BASE_URL = 'https://agentsearch-api.vercel.app';
const LEGACY_OVERRIDE = process.env.FLYBASIS_MCP_FORCE_LEGACY === '1';
const UPSTREAM_BASE = (process.env.FLYBASIS_MCP_API_BASE_URL || '').trim();
const LEGACY_MODE = LEGACY_OVERRIDE || (UPSTREAM_BASE && UPSTREAM_BASE !== DEFAULT_BASE_URL);
const PROXY_SECRET = process.env.FLYBASIS_MCP_PROXY_SECRET || process.env.AGENTSEARCH_MCP_PROXY_SECRET || '';
const SERPER_KEY = process.env.FLYBASIS_MCP_SERPER_KEY || '';
const BRAVE_KEY = process.env.FLYBASIS_MCP_BRAVE_KEY || '';
const USER_AGENT =
  process.env.FLYBASIS_MCP_USER_AGENT ||
  'flybasis-mcp/1.0 (+https://github.com/Adhambadrun/SpicyTool/tree/main/flybasis-mcp)';
const FETCH_TIMEOUT_MS = 20_000;
const MAX_FETCH_BYTES = 1_500_000;

const TEXT_TOOLS = { readOnlyHint: true, destructiveHint: false, idempotentHint: true, openWorldHint: true };

// ---------------------------------------------------------------- helpers ---

function asText(payload) {
  return {
    content: [{ type: 'text', text: typeof payload === 'string' ? payload : JSON.stringify(payload, null, 2) }],
  };
}

function asError(err) {
  const message = err?.message || String(err || 'unknown error');
  return {
    content: [{ type: 'text', text: JSON.stringify({ error: message, details: err?.details || null }, null, 2) }],
    isError: true,
  };
}

function decodeEntities(s) {
  if (!s) return '';
  return String(s)
    .replace(/&amp;/g, '&')
    .replace(/&lt;/g, '<')
    .replace(/&gt;/g, '>')
    .replace(/&quot;/g, '"')
    .replace(/&#0?39;/g, "'")
    .replace(/&apos;/g, "'")
    .replace(/&nbsp;/g, ' ')
    .replace(/&#(\d+);/g, (_, n) => String.fromCodePoint(Number(n)))
    .replace(/&#x([0-9a-f]+);/gi, (_, n) => String.fromCodePoint(parseInt(n, 16)))
    .replace(/\s+/g, ' ')
    .trim();
}

function stripTags(s) {
  return decodeEntities(String(s || '').replace(/<[^>]*>/g, ' '));
}

/** Remove boilerplate elements, then collapse block boundaries to newlines. */
function htmlToText(html) {
  let out = String(html || '');
  // Remove the noisy blocks entirely (including their contents).
  out = out.replace(
    /<(script|style|noscript|template|svg|iframe|button|form|canvas|audio|video|object|embed)[^>]*>[\s\S]*?<\/\1>/gi,
    ' '
  );
  out = out.replace(
    /<(nav|footer|header|aside|menu|dialog)[^>]*>[\s\S]*?<\/\1>/gi,
    ' '
  );
  // Turn structural tags into line breaks.
  out = out.replace(
    /<\/(p|div|li|tr|h[1-6]|section|article|table|ul|ol|blockquote|pre|br|hr|td|th)>/gi,
    '\n'
  );
  out = out.replace(/<(br|hr)[^>]*>/gi, '\n');
  out = out.replace(/<[^>]*>/g, ' ');
  return decodeEntities(out)
    .split('\n')
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n');
}

function htmlToMarkdown(html, baseUrl) {
  let text = String(html || '').replace(
    /<(script|style|noscript|template|svg|iframe|button|form|canvas|audio|video|object|embed|nav|footer|header|aside|menu|dialog)[^>]*>[\s\S]*?<\/\1>/gi,
    ' '
  );
  // Preserve headings.
  text = text.replace(/<h([1-6])[^>]*>([\s\S]*?)<\/h\1>/gi, (_, level, inner) => `\n${'#'.repeat(Number(level))} ${stripTags(inner)}\n`);
  text = text.replace(/<li[^>]*>([\s\S]*?)<\/li>/gi, (_, inner) => `\n- ${stripTags(inner)}\n`);
  // Turn remaining anchors into markdown links when possible.
  text = text.replace(/<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi, (_, href, inner) => {
    const label = stripTags(inner);
    let target = href;
    try {
      target = new URL(href, baseUrl).toString();
    } catch {
      /* keep as-is */
    }
    if (!label || label === target) return ` ${target} `;
    return ` [${label}](${target}) `;
  });
  text = text.replace(/<p[^>]*>([\s\S]*?)<\/p>/gi, (_, inner) => `\n${stripTags(inner)}\n`);
  text = text.replace(/\n{3,}/g, '\n\n');
  return htmlToText(text.replace(/<[^>]*>/g, ' '))
    .split('\n')
    .map((line) => line.replace(/\s+/g, ' ').trim())
    .filter(Boolean)
    .join('\n');
}

function domainOf(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, '');
  } catch {
    return '';
  }
}

/** Decode DuckDuckGo's internal redirect links (duckduckgo.com/l/?uddg=...). */
function resolveDdgHref(href) {
  let clean = String(href || '').trim();
  try {
    const u = new URL(clean);
    if (u.hostname.endsWith('duckduckgo.com') && u.searchParams.get('uddg')) {
      clean = u.searchParams.get('uddg');
    }
    if (clean.startsWith('//')) clean = `https:${clean}`;
    return clean;
  } catch {
    return clean;
  }
}

/** Best-effort SSRF guard: public http(s) URLs only. */
function assertPublicUrl(rawUrl) {
  let u;
  try {
    u = new URL(String(rawUrl));
  } catch {
    throw new Error('Invalid URL');
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') {
    throw new Error('Only http/https URLs are allowed');
  }
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (host === 'localhost' || host.endsWith('.localhost') || host.endsWith('.local') || host.endsWith('.internal')) {
    throw new Error('Private hostnames are not allowed');
  }
  const literal = /^(\d{1,3}\.){3}\d{1,3}$/.test(host)
    ? host.split('.').map(Number)
    : null;
  if (literal) {
    const [a, b] = literal;
    if (a === 0) throw new Error('Reserved address ranges are not allowed');
    if (a === 10 || a === 127) throw new Error('Private/loopback addresses are not allowed');
    if (a === 172 && b >= 16 && b <= 31) throw new Error('Private address range is not allowed');
    if (a === 192 && b === 168) throw new Error('Private address range is not allowed');
    if (a === 169 && b === 254) throw new Error('Link-local address range is not allowed');
  }
  return u;
}

async function assertPublicDns(u) {
  const host = u.hostname.toLowerCase().replace(/^\[|\]$/g, '');
  if (/^\d{1,3}(\.\d{1,3}){3}$/.test(host)) return; // literal already checked
  try {
    const records = await dnsLookup(host, { all: true });
    for (const { address } of records) {
      const parts = address.split('.').map(Number);
      if (parts[0] === 10 || parts[0] === 127) throw new Error('Private/loopback address resolved from hostname');
      if (parts[0] === 172 && parts[1] >= 16 && parts[1] <= 31) throw new Error('Private address resolved from hostname');
      if (parts[0] === 192 && parts[1] === 168) throw new Error('Private address resolved from hostname');
      if (parts[0] === 169 && parts[1] === 254) throw new Error('Link-local address resolved from hostname');
    }
  } catch (err) {
    if (err instanceof Error && /Private|resolved|Invalid|ENOTFOUND/i.test(err.message)) throw err;
    throw new Error('Hostname resolution failed');
  }
}

async function fetchText(url, { accept = 'text/html', qs = {} } = {}) {
  const target = new URL(url);
  for (const [k, v] of Object.entries(qs)) {
    if (v !== undefined && v !== null && v !== '') target.searchParams.set(k, String(v));
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(target, {
      headers: {
        'user-agent': USER_AGENT,
        accept,
        'accept-language': 'en-US,en;q=0.9',
      },
      signal: controller.signal,
      redirect: 'follow',
    });
    if (!res.ok) {
      const err = new Error(`Upstream returned HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    const len = Number(res.headers.get('content-length') || 0);
    if (len > MAX_FETCH_BYTES) throw new Error(`Response too large (${len} bytes)`);
    const reader = res.body?.getReader();
    let text;
    if (!reader) {
      text = await res.text();
    } else {
      const chunks = [];
      let total = 0;
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        total += value.byteLength;
        if (total > MAX_FETCH_BYTES) throw new Error('Response exceeded size limit');
        chunks.push(value);
      }
      text = Buffer.concat(chunks).toString('utf8');
    }
    return { text, finalUrl: res.url || target.toString() };
  } finally {
    clearTimeout(timer);
  }
}

// ------------------------------------------------------------- providers ---

/** DuckDuckGo Lite HTML -> normalized results. Returns null when unavailable. */
async function searchDuckDuckGoLite(q, { limit = 10, country = '' } = {}) {
  const { text: html } = await fetchText('https://lite.duckduckgo.com/lite/', {
    qs: { q, kl: country ? `us-${country}` : '' },
  });
  return parseDdgLiteHtml(html, { limit });
}

/** Pure parser for DuckDuckGo Lite HTML (no network — used by tests too). */
export function parseDdgLiteHtml(html, { limit = 10 } = {}) {
  if (/challenge/i.test(html) && /fieldset/i.test(html)) {
    throw new Error('DuckDuckGo is rate-limiting automated queries right now');
  }
  if (!/result-link/i.test(html)) {
    return { results: [], zeroClick: null, adsFiltered: 0 };
  }
  const results = [];
  let adsFiltered = 0;
  const anchors = Array.from(html.matchAll(/<a[^>]*class="[^"]*result-link[^"]*"[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi));
  for (const m of anchors) {
    const rawHref = m[1];
    const title = stripTags(m[2]);
    if (!title) continue;
    if (/duckduckgo\.com\/y\.js|aclick|bing\.com\/aclick/i.test(rawHref)) {
      adsFiltered += 1;
      continue;
    }
    const url = resolveDdgHref(rawHref);
    if (!/^https?:\/\//i.test(url)) continue;
    // Grab the snippet: the closest result-snippet cell after this anchor.
    const after = html.slice(m.index + m[0].length, m.index + m[0].length + 4000);
    const snip = after.match(/class="result-snippet"[^>]*>([\s\S]*?)<\/td>/i);
    results.push({
      position: results.length + 1,
      title,
      url,
      snippet: stripTags(snip ? snip[1] : ''),
      source: 'duckduckgo',
      domain: domainOf(url),
    });
    if (results.length >= Math.min(limit, 20)) break;
  }
  // Zero-click (instant) block, if present.
  let zeroClick = null;
  const zc = html.match(/<div[^>]*class="[^"]*zero-click[^"]*"[^>]*>([\s\S]*?)<\/div>/i);
  if (zc) {
    const link = zc[1].match(/<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/i);
    const heading = zc[1].match(/<b[^>]*>([\s\S]*?)<\/b>/i);
    if (link && heading) {
      zeroClick = {
        heading: stripTags(heading[1]),
        text: stripTags(zc[1]).replace(/^.*?:\s*/, ''),
        sourceUrl: link[1],
        source: 'duckduckgo',
      };
    }
  }
  return { results, zeroClick, adsFiltered };
}

/** Wikipedia OpenSearch fallback — always keyless, no bot protection. */
async function searchWikipedia(q, { limit = 10 } = {}) {
  const { text: json } = await fetchText('https://en.wikipedia.org/w/api.php', {
    accept: 'application/json',
    qs: {
      action: 'query',
      list: 'search',
      srsearch: q,
      srlimit: Math.min(Math.max(limit, 1), 20),
      srprop: 'snippet',
      format: 'json',
      origin: '*',
    },
  });
  const body = JSON.parse(json);
  const hits = body?.query?.search || [];
  return hits.map((hit, i) => ({
    position: i + 1,
    title: hit.title,
    url: `https://en.wikipedia.org/wiki/${encodeURIComponent(String(hit.title).replace(/ /g, '_'))}`,
    snippet: stripTags(hit.snippet || ''),
    source: 'wikipedia',
    domain: 'en.wikipedia.org',
  }));
}

/** Optional operator-supplied SERP keys (kept in the repo as config, no code change). */
async function searchSerper(q, { limit = 10, country = '' } = {}) {
  if (!SERPER_KEY) return null;
  const res = await fetch('https://google.serper.dev/search', {
    method: 'POST',
    headers: { 'content-type': 'application/json', 'x-api-key': SERPER_KEY, 'user-agent': USER_AGENT },
    body: JSON.stringify({ q, num: Math.min(limit, 20), gl: country || 'us' }),
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`Serper returned HTTP ${res.status}`);
  const body = await res.json();
  return (body.organic || []).slice(0, limit).map((r, i) => ({
    position: i + 1,
    title: r.title,
    url: r.link,
    snippet: r.snippet,
    source: 'serper',
    domain: domainOf(r.link),
  }));
}

async function searchBrave(q, { limit = 10, country = '' } = {}) {
  if (!BRAVE_KEY) return null;
  const url = new URL('https://api.search.brave.com/res/v1/web/search');
  url.searchParams.set('q', q);
  url.searchParams.set('count', String(Math.min(limit, 20)));
  if (country) url.searchParams.set('country', country.toUpperCase());
  const res = await fetch(url, {
    headers: { accept: 'application/json', 'x-subscription-token': BRAVE_KEY, 'user-agent': USER_AGENT },
    signal: AbortSignal.timeout(FETCH_TIMEOUT_MS),
  });
  if (!res.ok) throw new Error(`Brave returned HTTP ${res.status}`);
  const body = await res.json();
  return (body.web?.results || []).slice(0, limit).map((r, i) => ({
    position: i + 1,
    title: r.title,
    url: r.url,
    snippet: r.description,
    source: 'brave',
    domain: domainOf(r.url),
  }));
}

/** DuckDuckGo Instant Answer JSON -> normalized; null when no answer. */
async function instantAnswerDuckDuckGo(q) {
  const { text } = await fetchText('https://api.duckduckgo.com/', {
    accept: 'application/json',
    qs: { q, format: 'json', no_html: '1', no_redirect: '1', skip_disambig: '0' },
  });
  const body = JSON.parse(text);
  const heading = body.Heading || '';
  const type = body.Answer
    ? 'answer'
    : body.AbstractText
      ? 'abstract'
      : Array.isArray(body.RelatedTopics) && body.RelatedTopics.length
        ? 'disambiguation'
        : 'none';
  const relatedTopics = [];
  const walk = (topics) => {
    for (const t of topics || []) {
      if (t.Topics) walk(t.Topics);
      else if (t.Text && t.FirstURL) {
        relatedTopics.push({ name: t.Name || '', text: t.Text, url: t.FirstURL });
      }
    }
  };
  walk(body.RelatedTopics);
  return {
    query: q,
    heading,
    type,
    text: body.Answer || body.AbstractText || body.Definition || '',
    source: body.AnswerSource || body.AbstractSource || body.DefinitionSource || '',
    sourceUrl: body.AbstractURL || body.DefinitionURL || '',
    image: body.Image && typeof body.Image === 'string' ? body.Image : '',
    relatedTopics: relatedTopics.slice(0, 25),
    provider: 'duckduckgo',
  };
}

/** Wikipedia REST summary fallback for instant answers. */
async function instantAnswerWikipedia(q) {
  const { text } = await fetchText(`https://en.wikipedia.org/api/rest_v1/page/summary/${encodeURIComponent(q)}`, {
    accept: 'application/json',
  });
  const body = JSON.parse(text);
  if (body.type === 'disambiguation') {
    return {
      query: q,
      heading: body.title || q,
      type: 'disambiguation',
      text: '',
      source: 'Wikipedia',
      sourceUrl: body.content_urls?.desktop?.page || '',
      image: '',
      relatedTopics: [],
      provider: 'wikipedia',
    };
  }
  return {
    query: q,
    heading: body.title || body.displaytitle || q,
    type: 'abstract',
    text: body.extract || '',
    source: 'Wikipedia',
    sourceUrl: body.content_urls?.desktop?.page || '',
    image: body.thumbnail?.source || '',
    relatedTopics: [],
    provider: 'wikipedia',
  };
}

// -------------------------------------------------------------- upstream ---

async function callUpstream(path, params) {
  const url = new URL(`${LEGACY_MODE ? UPSTREAM_BASE : DEFAULT_BASE_URL}${path}`);
  for (const [k, v] of Object.entries(params)) {
    if (v === undefined || v === null || v === '') continue;
    url.searchParams.set(k, String(v));
  }
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 25_000);
  try {
    const headers = { accept: 'application/json' };
    if (PROXY_SECRET) headers['X-RapidAPI-Proxy-Secret'] = PROXY_SECRET;
    const res = await fetch(url, { headers, signal: controller.signal });
    const text = await res.text();
    let body = null;
    try {
      body = text ? JSON.parse(text) : null;
    } catch {
      body = { raw: text };
    }
    if (!res.ok) {
      const err = new Error(body?.error?.message || `Upstream HTTP ${res.status}`);
      err.status = res.status;
      throw err;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}

// ----------------------------------------------------------- tool bodies ---

async function doWebSearch(args = {}) {
  if (LEGACY_MODE) {
    return asText(
      await callUpstream('/v1/search', {
        q: args.q,
        provider: args.provider,
        limit: args.limit,
        country: args.country,
      })
    );
  }
  const q = String(args.q || '').trim();
  if (!q) return asError(new Error('q is required'));
  const limit = Math.min(Math.max(Number(args.limit) || 10, 1), 20);
  const provider = args.provider || '';
  const t0 = Date.now();
  const wanted = provider ? [provider] : ['serper', 'brave', 'duckduckgo', 'wikipedia'];
  const errors = [];
  let results = [];
  let zeroClick = null;
  let adsFiltered = 0;
  let used = '';
  for (const p of wanted) {
    try {
      if (p === 'serper') {
        const r = await searchSerper(q, { limit, country: args.country });
        if (r) {
          results = r;
          used = 'serper';
        }
      } else if (p === 'brave') {
        const r = await searchBrave(q, { limit, country: args.country });
        if (r) {
          results = r;
          used = 'brave';
        }
      } else if (p === 'duckduckgo') {
        const r = await searchDuckDuckGoLite(q, { limit, country: args.country });
        results = r.results;
        zeroClick = r.zeroClick;
        adsFiltered = r.adsFiltered;
        if (results.length) used = p;
        else errors.push('duckduckgo returned no organic results');
      } else if (p === 'wikipedia') {
        results = await searchWikipedia(q, { limit });
        used = p;
      }
      if (results.length) break;
    } catch (err) {
      errors.push(`${p}: ${err.message}`);
    }
  }
  if (!results.length) {
    return asError(new Error(`All search providers failed. ${errors.join('; ') || 'no results'}`));
  }
  return asText({
    results,
    zeroClick,
    meta: {
      query: q,
      provider: used,
      sources: [...new Set(results.map((r) => r.source))],
      cache_state: 'live',
      took_ms: Date.now() - t0,
      ads_filtered: adsFiltered,
      errors,
    },
  });
}

async function doInstantAnswer(args = {}) {
  if (LEGACY_MODE) {
    return asText(await callUpstream('/v1/answer', { q: args.q }));
  }
  const q = String(args.q || '').trim();
  if (!q) return asError(new Error('q is required'));
  const t0 = Date.now();
  try {
    const answer = await instantAnswerDuckDuckGo(q);
    return asText({ ...answer, meta: { provider: 'duckduckgo', cache_state: 'live', took_ms: Date.now() - t0 } });
  } catch (primaryErr) {
    try {
      const fallback = await instantAnswerWikipedia(q);
      return asText({ ...fallback, meta: { provider: 'wikipedia', fallback_of: 'duckduckgo', took_ms: Date.now() - t0 } });
    } catch (fallbackErr) {
      return asError(new Error(`Instant answer failed: ${primaryErr.message}; fallback: ${fallbackErr.message}`));
    }
  }
}

async function doFetchUrl(args = {}) {
  if (LEGACY_MODE) {
    return asText(await callUpstream('/v1/fetch', { url: args.url, format: args.format, maxChars: args.maxChars, links: args.links }));
  }
  const rawUrl = String(args.url || '').trim();
  if (!rawUrl) return asError(new Error('url is required'));
  const format = args.format === 'markdown' ? 'markdown' : 'text';
  const maxChars = Math.min(Math.max(Number(args.maxChars) || 100000, 500), 500000);
  const wantLinks = Boolean(args.links);
  try {
    const u = assertPublicUrl(rawUrl);
    await assertPublicDns(u);
    const t0 = Date.now();
    const { text: html, finalUrl } = await fetchText(u.toString(), { accept: 'text/html,application/xhtml+xml' });
    const title = stripTags((html.match(/<title[^>]*>([\s\S]*?)<\/title>/i) || [])[1] || '');
    const content =
      format === 'markdown' ? htmlToMarkdown(html, u.toString()) : htmlToText(html);
    const trimmed = content.length > maxChars ? `${content.slice(0, maxChars)}\n…[truncated]` : content;
    let links = [];
    if (wantLinks) {
      const seen = new Set();
      for (const m of html.matchAll(/<a[^>]*href="([^"]+)"[^>]*>([\s\S]*?)<\/a>/gi)) {
        try {
          const abs = new URL(m[1], u.toString()).toString();
          if (!/^https?:/i.test(abs)) continue;
          if (seen.has(abs)) continue;
          seen.add(abs);
          links.push({ text: stripTags(m[2]).slice(0, 120), url: abs, domain: domainOf(abs) });
          if (links.length >= 50) break;
        } catch {
          /* skip malformed */
        }
      }
    }
    return asText({
      url: rawUrl,
      finalUrl,
      title,
      format,
      length: trimmed.length,
      content: trimmed,
      links,
      meta: { provider: 'self-hosted-fetch', cache_state: 'live', took_ms: Date.now() - t0 },
    });
  } catch (err) {
    return asError(err);
  }
}

// ---------------------------------------------------------------- wiring ---

/** Register the three MCP tools on a server. */
export function registerTools(server) {
  server.registerTool(
    'web_search',
    {
      title: 'Web search (SERP)',
      description:
        'Search the web and return normalized organic results (position, title, url, snippet, source, domain) plus a meta envelope. Keyless by default: uses DuckDuckGo Lite with an automatic Wikipedia fallback. If the operator sets FLYBASIS_MCP_BRAVE_KEY/FLYBASIS_MCP_SERPER_KEY, those providers are used instead. Zero API keys are required. Use this for a full web SERP; use instant_answer for quick facts/definitions.',
      inputSchema: {
        q: z.string().describe('Search query.'),
        provider: z.enum(['brave', 'serper', 'duckduckgo', 'wikipedia']).optional().describe('Force a provider. Omit to use whichever is configured (serper > brave > duckduckgo > wikipedia).'),
        limit: z.number().int().min(1).max(20).optional().describe('Max results, clamped to 1-20. Default 10.'),
        country: z.string().optional().describe('ISO-3166 alpha-2 region, lowercase. Default us.'),
      },
      annotations: TEXT_TOOLS,
    },
    doWebSearch
  );

  server.registerTool(
    'instant_answer',
    {
      title: 'Instant answer (keyless)',
      description:
        'Keyless DuckDuckGo Instant Answer API — definitions, entities, and quick facts, with a Wikipedia summary fallback. Returns heading, type (abstract/answer/disambiguation), text, source, sourceUrl, optional image and related topics, plus a meta envelope. Not a full web SERP; use web_search for that. No API key required.',
      inputSchema: {
        q: z.string().describe('Query.'),
      },
      annotations: TEXT_TOOLS,
    },
    doInstantAnswer
  );

  server.registerTool(
    'fetch_url',
    {
      title: 'Fetch a URL as clean text/markdown (RAG-ready)',
      description:
        'Fetches any public http(s) URL, strips boilerplate (scripts, nav, footer, ads), and returns clean text or markdown ready for an LLM context window. SSRF-guarded — refuses private/loopback/internal hosts, including after DNS resolution. Returns url, title, format, length, content (and finalUrl plus up to 50 extracted links when links=true), with a meta envelope. No API key required.',
      inputSchema: {
        url: z.string().describe('The URL to fetch (public http/https only).'),
        format: z.enum(['text', 'markdown']).optional().describe('Output format. Default text.'),
        maxChars: z.number().int().min(500).max(500000).optional().describe('Max characters of content returned, clamped 500-500000. Default 100000.'),
        links: z.boolean().optional().describe('Also return up to 50 extracted links. Default false.'),
      },
      annotations: TEXT_TOOLS,
    },
    doFetchUrl
  );
}

export const BASE_URL_FOR_HEALTH = LEGACY_MODE ? UPSTREAM_BASE : 'keyless-self-hosted';
export const TOOL_MODE = LEGACY_MODE ? 'legacy-upstream' : 'keyless';
export const CONFIGURED_PROVIDERS = [
  'serper',
  'brave',
  'duckduckgo',
  'wikipedia',
].filter((p) => (p === 'serper' && SERPER_KEY) || (p === 'brave' && BRAVE_KEY) || p === 'duckduckgo' || p === 'wikipedia');

// Exported for offline tests (pure, network-free).
export { htmlToText, htmlToMarkdown, resolveDdgHref, assertPublicUrl, domainOf };
export const PURE = { htmlToText, htmlToMarkdown, resolveDdgHref, assertPublicUrl, domainOf, parseDdgLiteHtml };
