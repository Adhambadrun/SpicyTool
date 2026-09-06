// frontend/test/search-stream-race.mjs — regression test for overlapping searches.
//
// Boots the REAL frontend/index.html in jsdom with a scripted EventSource and
// drives the actual runSearch()/searchOneDate()/absorbResults() code — no
// reimplementation of the logic under test.
//
// What must hold:
//   1. Starting a new search while an old one is still streaming must cut the
//      old stream off: stale flights must NEVER land in the new result list.
//   2. Only the newest search may finalize `state.streaming` / searchFailed —
//      a superseded stream must not end the new search's skeleton early.
//   3. The newest stream's results and completion state win, unchanged.
//
// Run:  cd frontend/test && npm install && node search-stream-race.mjs
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

// ---- scripted EventSource: instances register globally so the test can emit
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
    w.scrollTo = () => {};   // jsdom does not implement scrolling
  },
});
const w = dom.window;

let failures = 0;
const check = (label, cond, detail = '') => {
  if (cond) console.log(`  ok   ${label}${detail ? `  (${detail})` : ''}`);
  else { failures++; console.log(`  FAIL ${label}${detail ? `  (${detail})` : ''}`); }
};

// A minimal one-way AwardResult shaped exactly like the v2 stream emits.
const result = (id) => ({
  id,
  source_provider: 'Flybasis',
  provenance: ['Flybasis'],
  airline: 'Egyptair', airline_code: 'MS', flight_number: 'MS 777',
  alliance: 'Star Alliance',
  route: {
    origin: 'CAI', destination: 'LHR',
    departure_time: '2026-10-01T10:00', arrival_time: '2026-10-01T14:00',
    duration_minutes: 240, stops: 0, distance_miles: 1000,
    segments: [{
      carrier: 'MS', marketing_carrier: null, flight_number: 'MS 777',
      aircraft: 'B787-9', origin: 'CAI', destination: 'LHR',
      departure_time: '2026-10-01T10:00', arrival_time: '2026-10-01T14:00',
      duration_minutes: 240, cabin_class: 'business', distance_miles: 1000,
    }],
    layovers: [],
  },
  cabin_class: 'business', mixed_cabin: false, ticket_type: 'award',
  pricing: {
    points: 30000, cash_fees: 50, currency: 'USD',
    program_name: 'Turkish Miles&Smiles', program_code: 'TK_MILESSMILES',
    cents_per_point: 1.4, retail_cash_usd: 600,
  },
  transfer_partners: [], seats_remaining: 2,
});
const START = (providers) => ({
  status: 'start', providers, live: false, live_providers: [],
  notice: 'no live provider', query: { routes: [['CAI', 'LHR']], trip: 'oneway' },
});
const DATA = (id) => ({
  status: 'data', provider: 'Flybasis', leg: 'out', route: ['CAI', 'LHR'],
  date: '2026-10-01', ok: true, cached: false, latency_ms: 5, error: null,
  progress: 0.5, count: 1, results: [result(id)],
});
const COMPLETE = (ids) => ({
  status: 'complete', elapsed_ms: 10, live: false, live_providers: [],
  notice: 'no live provider', providers: [], dedupe: {},
  count: ids.length, results: ids.map(result),
});

setTimeout(async () => {
  const tick = () => new Promise((r) => setTimeout(r, 0));

  check('page booted without JS errors', errors.length === 0, errors.slice(0, 2).join(' | '));

  // --- search #1 starts (old route) ----------------------------------------
  w.eval(`state.origins = ["CAI"]; state.destinations = ["LHR"];`);
  w.eval(`startSearch()`);
  await tick();
  check('search #1 opened one stream', sources.length === 1, `${sources.length}`);
  const oldStream = sources[0];

  // --- search #2 starts while #1 is still in flight ------------------------
  w.eval(`state.origins = ["JFK"]; state.destinations = ["LHR"];`);
  w.eval(`startSearch()`);
  await tick();
  check('search #2 opened a second stream', sources.length === 2, `${sources.length}`);
  const newStream = sources[1];

  // --- the OLD stream keeps emitting after search #2 began -----------------
  oldStream.emit('start', START(['Flybasis']));
  oldStream.emit('data', DATA('STALE-1'));
  oldStream.emit('complete', COMPLETE(['STALE-1']));
  await tick();

  const staleIn = w.eval(`state.results.map(r => r.id).includes("STALE-1")`);
  check('stale stream results do NOT contaminate the new search', !staleIn,
    staleIn ? 'STALE-1 leaked into the new result list' : 'clean');

  // --- the NEW stream resolves normally ------------------------------------
  newStream.emit('start', START(['Flybasis']));
  newStream.emit('data', DATA('FRESH-1'));
  newStream.emit('complete', COMPLETE(['FRESH-1']));
  await tick();

  const ids = w.eval(`JSON.stringify(state.results.map(r => r.id))`);
  check('new search keeps its own results', ids === JSON.stringify(['FRESH-1']), ids);
  check('streaming ended only after the newest stream completed',
    w.eval('state.streaming') === false);
  check('searchFailed reflects the newest stream', w.eval('state.searchFailed') === false);

  // --- a superseded stream is closed, not left running ---------------------
  check('superseded stream socket was closed',
    oldStream.closed === true, oldStream.closed ? '' : 'old EventSource still open');

  check('no JS errors during the race', errors.length === 0, errors.slice(0, 2).join(' | '));

  console.log(failures === 0 ? '\nsearch-stream-race: PASS' : `\nsearch-stream-race: ${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}, 250);
