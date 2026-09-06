// frontend/test/no-provider-empty.mjs — regression test for the "no live award
// provider connected" empty state.
//
// The symptom this locks out: a search with no credential behind it showed a
// bare headline with the explanation deliberately HIDDEN (#empty-msg hidden),
// while the API's own `notice` — fetched into state.notice on every stream
// event — was never rendered anywhere. A user staring at "No live award
// provider connected" had no reason and no next step.
//
// What must hold:
//   1. A disabled provider names the reason (the notice, verbatim) and the
//      exact variable to set (FLYBASIS_API_KEY) — and it is VISIBLE.
//   2. Re-renders during a stream never stack duplicate hints.
//   3. A provider that answered but failed (timeout) surfaces that error and
//      is NOT told to go hunting for a key.
//   4. A genuinely-empty live search keeps its "no availability" advice.
//   5. Upstream copy is rendered as text, never markup (no XSS via error).
//
// Boots the REAL frontend/index.html in jsdom and drives the actual
// runSearch()/renderResults() code through a scripted EventSource.
//
// Run:  cd frontend/test && npm install && node no-provider-empty.mjs
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

const sources = [];
class FakeEventSource {
  constructor(url) {
    this.url = url;
    this.handlers = {};
    this.closed = false;
    sources.push(this);
  }
  addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); }
  emit(name, data) {
    if (this.closed) return;
    for (const fn of this.handlers[name] || []) fn({ data: JSON.stringify(data) });
  }
  close() { this.closed = true; }
}

const dom = new JSDOM(html, {
  runScripts: 'dangerously',
  pretendToBeVisual: true,
  url: 'http://localhost:8000/',
  virtualConsole: vc,
  beforeParse(w) {
    w.fetch = async () => ({ ok: false, status: 0, json: async () => ({}) });
    w.EventSource = FakeEventSource;
    w.scrollTo = () => {};
  },
});
const w = dom.window;
const d = w.document;

let failures = 0;
const check = (label, cond, detail = '') => {
  if (cond) console.log(`  ok   ${label}${detail ? `  (${detail})` : ''}`);
  else { failures++; console.log(`  FAIL ${label}${detail ? `  (${detail})` : ''}`); }
};

const DISABLED_FLYBASIS = {
  provider: 'Flybasis', ok: false, cached: false, latency_ms: 0, count: 0,
  error: 'Disabled: no credential for Flybasis (https://enterprise-api.flybasis.com). '
       + 'Set the FLYBASIS_API_KEY environment variable to a key issued to you by '
       + 'Flybasis to enable this provider.',
};
const NOTICE = 'No live award data is available. SpicyTool relays the Flybasis search '
  + 'engine only — set FLYBASIS_API_KEY to a key issued to you by Flybasis to search '
  + 'real availability.';

/* Drive one complete search through the real code path and return the stream. */
async function runSearchWith(eventOverrides) {
  const before = sources.length;
  w.eval('startSearch()');
  await new Promise((r) => setTimeout(r, 0));
  const stream = sources[before];
  const base = {
    status: 'complete', elapsed_ms: 3, live: false, live_providers: [],
    notice: NOTICE, providers: [DISABLED_FLYBASIS], dedupe: {}, count: 0, results: [],
  };
  const ev = { ...base, ...eventOverrides };
  stream.emit('start', { ...ev, status: 'start', query: { routes: [['JFK', 'LHR']], trip: 'oneway' } });
  stream.emit('complete', ev);
  await new Promise((r) => setTimeout(r, 0));
  return stream;
}

const text = (sel) => (d.querySelector(sel)?.textContent || '').trim();
const visible = (sel) => {
  const el = d.querySelector(sel);
  return !!el && el.hidden === false;
};

setTimeout(async () => {
  check('page booted without JS errors', errors.length === 0, errors.slice(0, 2).join(' | '));
  w.eval('state.origins = ["JFK"]; state.destinations = ["LHR"]; state.date = "2026-10-05"; '
       + 'state.trip = "oneway"; state.returnDate = null; state.flex = 0; state.cabin = "business";');

  // ---- 1. credential gap: reason + remedy, both visible -------------------
  await runSearchWith({});
  check('headline names the state', text('#empty h3') === 'No live award provider connected', text('#empty h3'));
  check('the empty card itself is visible', visible('#empty'));
  check('the explanation is NOT hidden', visible('#empty-msg'),
    d.querySelector('#empty-msg')?.hidden === true ? 'still hidden' : 'shown');
  check('the API notice is surfaced verbatim', text('#empty-msg') === NOTICE, text('#empty-msg').slice(0, 48) + '…');
  check('state.notice is actually rendered (was fetched then dropped)',
    text('#empty-msg').includes('Flybasis search engine only'));
  check('the remedy is visible', visible('#empty-hint'));
  check('the remedy names the exact variable', text('#empty-hint').includes('FLYBASIS_API_KEY'), text('#empty-hint').slice(0, 60) + '…');
  check('the variable is a <code> chip', !!d.querySelector('#empty-hint code'),
    d.querySelector('#empty-hint code') ? d.querySelector('#empty-hint code').textContent : 'no code element');
  check('the remedy says restart + re-search', text('#empty-hint').includes('restart'));
  check('it promises no invented availability', /never invents/i.test(text('#empty-hint')));

  // ---- 2. no stacking across the many re-renders a stream triggers --------
  const stream = sources[sources.length - 1];
  for (let i = 0; i < 3; i++) stream.emit('start', { status: 'start', providers: [DISABLED_FLYBASIS], live: false, live_providers: [], notice: NOTICE });
  w.eval('renderResults()');
  await new Promise((r) => setTimeout(r, 0));
  check('re-renders never stack hint nodes', d.querySelectorAll('#empty-hint').length === 1,
    `${d.querySelectorAll('#empty-hint').length} nodes`);
  check('hint text is not duplicated by re-renders',
    (text('#empty-hint').match(/FLYBASIS_API_KEY/g) || []).length === 1,
    `${(text('#empty-hint').match(/FLYBASIS_API_KEY/g) || []).length} occurrences`);

  // ---- 3. provider answered but failed: its error, not key advice --------
  await runSearchWith({
    notice: null,
    providers: [{ provider: 'Flybasis', ok: false, cached: false, latency_ms: 6000, count: 0,
                  error: 'Timeout: Flybasis did not respond within 6s' }],
  });
  check('an upstream failure is shown verbatim', text('#empty-msg') === 'Timeout: Flybasis did not respond within 6s', text('#empty-msg'));
  check('no key hunt when the credential is not the problem', !visible('#empty-hint'),
    visible('#empty-hint') ? 'hint wrongly shown' : 'correctly absent');

  // ---- 4. live search, genuinely no availability ------------------------
  await runSearchWith({
    live: true, live_providers: ['Flybasis'], notice: null,
    providers: [{ provider: 'Flybasis', ok: true, cached: false, latency_ms: 900, count: 0, error: null }],
  });
  check('an empty live search keeps its advice', text('#empty h3') === 'No award availability', text('#empty h3'));
  check('and is not told a provider is missing', text('#empty-msg').includes('Try nearby dates'), text('#empty-msg').slice(0, 60));
  check('no credential hint here either', !visible('#empty-hint'));

  // ---- 5. upstream copy can never become markup -------------------------
  const evil = 'Disabled: see <img src=x onerror="alert(1)"> at javascript:alert(1) — Set the FLYBASIS_API_KEY environment variable';
  await runSearchWith({ notice: `<b>bold</b> ${evil}`, providers: [{ provider: 'Flybasis', ok: false, error: evil, cached: false, latency_ms: 0, count: 0 }] });
  check('an upstream error renders as text, not markup', d.querySelector('#empty img') === null,
    d.querySelector('#empty img') ? 'img element injected' : 'no injected element');
  check('no <b> element from the notice', d.querySelector('#empty-msg b') === null);
  check('the raw string is still readable', text('#empty-msg').includes('<img src=x'));

  check('no JS errors while rendering the empty states', errors.length === 0, errors.slice(0, 2).join(' | '));

  console.log(failures === 0 ? '\nno-provider-empty: PASS' : `\nno-provider-empty: ${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}, 250);
