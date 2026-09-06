// frontend/test/web-context.mjs — regression test for the "Web context" panel.
//
// Boots the REAL frontend/index.html in jsdom and drives the actual
// wcHTML()/loadWebContext() code against the REAL payload shape the backend
// returns when AgentSearch serves the request. No reimplementation of the
// rendering logic under test.
//
// What must hold:
//   1. An AgentSearch-served payload renders its results (title/url/snippet).
//   2. The rendered panel names the serving backend, so the operator can see
//      which one answered.
//   3. Anything not explicitly labelled non-award is REFUSED — the panel must
//      never render award-looking data, whatever the server sends.
//
// Run:  cd frontend/test && npm install && node web-context.mjs
// Exit: 0 pass, 1 fail, 77 skip (jsdom not installed).

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

let JSDOM, VirtualConsole;
try {
  ({ JSDOM, VirtualConsole } = await import('jsdom'));
} catch {
  console.log('SKIP: jsdom is not installed (cd frontend/test && npm install)');
  process.exit(77);
}

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'index.html'), 'utf8');

const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', (e) => errors.push(`jsdomError: ${e.message}`));
vc.on('error', (...a) => errors.push(`console.error: ${a.join(' ')}`));

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'http://localhost:8000/',
  virtualConsole: vc,
  beforeParse(w) {
    w.fetch = async () => ({ ok: false, status: 0, json: async () => ({}) });
  },
});
const w = dom.window;

let failures = 0;
const check = (label, cond, detail = '') => {
  if (cond) console.log(`  ok   ${label}${detail ? `  (${detail})` : ''}`);
  else { failures++; console.log(`  FAIL ${label}${detail ? `  (${detail})` : ''}`); }
};

// The exact envelope backend/services/web_context.py emits via AgentSearch.
const AGENTSEARCH_PAYLOAD = {
  kind: 'web_context',
  is_award_data: false,
  source: 'agentsearch',
  endpoint: 'https://agentsearch.p.rapidapi.com/v1/search',
  tool: 'web_search',
  disclaimer:
    'Web context from the AgentSearch web-search API (RapidAPI). This is general '
    + 'web data — search results and page snippets — NOT award availability, '
    + 'pricing or seat counts. It never contributes to search results.',
  ok: true,
  error: null,
  data: {
    query: 'JFK to LHR business class award booking transfer partners guide',
    results: [
      {
        position: 1,
        title: 'Claude — Anthropic',
        url: 'https://www.anthropic.com/claude',
        snippet: 'Claude is a family of large language models built by Anthropic.',
        source: 'brave',
        domain: 'anthropic.com',
        published: null,
      },
      {
        position: 2,
        title: 'Award booking guide',
        url: 'https://thepointsguy.com/guide/award-booking/',
        snippet: 'Transfer partners and sweet spots.',
        source: 'brave',
        domain: 'thepointsguy.com',
        published: null,
      },
    ],
    meta: { provider: 'brave', count: 2, took_ms: 187, cached: false, stale: false },
  },
};


// A REAL captured production row set (2026-09-06, provider=brave). Verifies the
// renderer against actual upstream output — including an ampersand in a title
// and a query-string URL, both of which must be escaped, not mangled.
const LIVE_CAPTURE = {
  kind: 'web_context', is_award_data: false, source: 'agentsearch',
  endpoint: 'https://agentsearch.p.rapidapi.com/v1/search', tool: 'web_search',
  disclaimer: 'Web context from the AgentSearch web-search API (RapidAPI). NOT award availability.',
  ok: true, error: null,
  data: {
    query: 'spicytool.vercel.app',
    results: [
      { position: 1, title: 'spice for sauce', url: 'https://spice-beryl.vercel.app/',
        snippet: 'spice for sauce', source: 'brave', domain: 'spice-beryl.vercel.app', published: null },
      { position: 5, title: 'SpicyTool - Apps on Google Play',
        url: 'https://play.google.com/store/apps/details?id=com.spicytool.app&hl=en',
        snippet: 'SpicyTool: the tool that does the heavy lifting for you.',
        source: 'brave', domain: 'play.google.com', published: '2026-05-31T00:00:00' },
      { position: 8, title: 'SpicyTool 2026 Pricing, Features, Reviews & Alternatives | GetApp',
        url: 'https://www.getapp.com/marketing-software/a/spicytool/',
        snippet: 'Spicytool is a cloud-based platform.', source: 'brave',
        domain: 'getapp.com', published: null },
    ],
    meta: { provider: 'brave', count: 3, took_ms: 692, cached: false, stale: false },
  },
};

setTimeout(() => {
  const render = (payload) => w.wcHTML(payload);

  // --- 1. a real AgentSearch payload renders ------------------------------
  const okHtml = render(AGENTSEARCH_PAYLOAD);
  check('renders both result titles',
    okHtml.includes('Claude — Anthropic') && okHtml.includes('Award booking guide'));
  check('renders result links',
    okHtml.includes('https://www.anthropic.com/claude'));
  check('renders snippets', okHtml.includes('large language models'));
  check('renders domains', okHtml.includes('thepointsguy.com'));
  check('links open safely (noopener + nofollow)',
    okHtml.includes('rel="noopener nofollow"'));

  // --- 2. the serving backend is visible ----------------------------------
  check('names the serving backend', okHtml.includes('via agentsearch'),
    okHtml.includes('via agentsearch') ? 'agentsearch' : 'MISSING');
  check('shows the non-award disclaimer', okHtml.includes('NOT award availability'));

  // --- 3. anything not labelled non-award is refused ----------------------
  const refused = 'Web context unavailable.';
  check('refuses is_award_data=true',
    render({ ...AGENTSEARCH_PAYLOAD, is_award_data: true }).includes(refused));
  check('refuses a wrong kind',
    render({ ...AGENTSEARCH_PAYLOAD, kind: 'search_results' }).includes(refused));
  check('refuses a missing label',
    render({ ok: true, data: { results: [{ title: 'x', url: 'https://x.io' }] } })
      .includes(refused));
  check('refuses null/undefined', render(null).includes(refused));

  // an upstream failure shows the reason, not a crash and not fake results
  const errHtml = render({
    ...AGENTSEARCH_PAYLOAD, ok: false, error: 'AgentSearch rejected the RapidAPI key (HTTP 401).', data: null,
  });
  check('surfaces an upstream error verbatim',
    errHtml.includes('rejected the RapidAPI key'));
  check('an error renders no result links', !errHtml.includes('<a '));

  // empty result set is stated, never faked
  check('empty results say so',
    render({ ...AGENTSEARCH_PAYLOAD, data: { results: [] } })
      .includes('No web results'));

  // a javascript: URL must never become a clickable link (esc() alone does not stop the scheme)
  const jsRow = render({
    ...AGENTSEARCH_PAYLOAD,
    data: { results: [{ position: 1, title: 'Evil row', url: 'javascript:alert(1)', snippet: 'x' }] },
  });
  check('javascript: URL renders as plain text, not a link', !jsRow.includes('<a ') && jsRow.includes('Evil row'));
  const relRow = render({
    ...AGENTSEARCH_PAYLOAD,
    data: { results: [{ position: 1, title: 'Proto-relative', url: '//evil.example/x', snippet: 'x' }] },
  });
  check('protocol-relative URL renders as plain text, not a link', !relRow.includes('<a '));


  // --- 4. a REAL production payload renders correctly ---------------------
  const liveHtml = render(LIVE_CAPTURE);
  check('live: renders all three captured rows',
    liveHtml.includes('spice for sauce')
    && liveHtml.includes('SpicyTool - Apps on Google Play')
    && liveHtml.includes('GetApp'));
  check('live: ampersand in a title is HTML-escaped',
    liveHtml.includes('Reviews &amp; Alternatives'),
    liveHtml.includes('Reviews &amp; Alternatives') ? 'escaped' : 'NOT ESCAPED');
  check('live: query-string URL kept intact and escaped',
    liveHtml.includes('id=com.spicytool.app&amp;hl=en'));
  check('live: no raw unescaped ampersand leaked into markup',
    !/&(?!(amp|lt|gt|quot|#3[49]);)/.test(liveHtml));

  check('no JS errors while rendering', errors.length === 0, errors.join(' | '));

  console.log(`\nweb-context: ${failures === 0 ? 'PASS' : 'FAIL'}`);
  process.exit(failures === 0 ? 0 : 1);
}, 250);
