// Drives the real Super HC label, auth lifecycle and search-completion refresh.
// All sessions and responses below are synthetic; no network access required.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM, VirtualConsole } from 'jsdom';

const here = path.dirname(fileURLToPath(import.meta.url));
const html = fs.readFileSync(path.join(here, '..', 'index.html'), 'utf8');
const errors = [];
const vc = new VirtualConsole();
vc.on('jsdomError', e => errors.push(e.message));
vc.on('error', (...args) => errors.push(args.join(' ')));
const payload = remaining => ({ provider: 'Flybasis', remaining, available: true, period: 'month' });
const reply = (data, status = 200) => ({ ok: status >= 200 && status < 300, status, json: async () => data });
const calls = [];
let quotaReply = () => reply(payload(3));
const sources = [];
class FakeEventSource {
  constructor(url) { this.url = url; this.handlers = {}; sources.push(this); }
  addEventListener(name, fn) { (this.handlers[name] ||= []).push(fn); }
  emit(name, data) { for (const fn of this.handlers[name] || []) fn({ data: JSON.stringify(data) }); }
  close() { this.closed = true; }
}
const dom = new JSDOM(html, {
  runScripts: 'dangerously', pretendToBeVisual: true,
  url: 'https://spicytool.test/', virtualConsole: vc,
  beforeParse(w) {
    w.sessionStorage.setItem('st_session', JSON.stringify({ email: 'owner@example.test', token: 'synthetic-app-session' }));
    w.fetch = async (url, options = {}) => {
      if (url === '/api/v2/super-hc/quota') {
        calls.push({ url, options });
        return quotaReply();
      }
      if (url.startsWith('/api/v1/auth/session')) return reply({ valid: true });
      if (url === '/api/v1/programs') return reply({ programs: [] });
      return reply([]);
    };
    w.EventSource = FakeEventSource;
    w.scrollTo = () => {};
  },
});
const w = dom.window;
const tick = () => new Promise(resolve => setTimeout(resolve, 0));
const label = () => w.document.getElementById('super-hc-quota')?.textContent;
const refresh = async (data, status = 200) => {
  quotaReply = () => reply(data, status);
  await w.eval('refreshSuperHcQuota(true)');
};
let failures = 0;
function check(name, condition) {
  console.log(`  ${condition ? 'ok  ' : 'FAIL'} ${name}`);
  if (!condition) failures++;
}

try {
  await tick();
  check('the label matches the requested monthly-count wording', label() === '(3 searches remaining for this month)');
  check('count appears beside Enable super hc mode',
    w.document.getElementById('adv-super').textContent.replace(/\s+/g, ' ').trim() ===
    'Enable super hc mode (3 searches remaining for this month)');
  check('updates are announced politely', w.document.getElementById('super-hc-quota').getAttribute('aria-live') === 'polite');
  check('startup coalesces duplicate quota reads', calls.length === 1);
  check('request is same-origin with app authorization in a header, not URL',
    calls[0].url === '/api/v2/super-hc/quota' && calls[0].options.headers.Authorization === 'Bearer synthetic-app-session');
  check('browser must not cache a spent quota', calls[0].options.cache === 'no-store');

  await refresh(payload(1));
  check('one uses singular search', label() === '(1 search remaining for this month)');
  await refresh(payload(0));
  check('zero is shown explicitly, not replaced by a fallback', label() === '(0 searches remaining for this month)');

  for (const remaining of [null, -1, '3', true, 1.5, '<img src=x onerror=alert(1)>']) {
    await refresh(payload(remaining));
    check(`invalid count ${JSON.stringify(remaining)} is unavailable`, label() === '(Remaining searches unavailable)');
  }
  for (const data of [null, {}, { ...payload(3), available: false }, { ...payload(3), provider: 'Other' }, { ...payload(3), period: 'day' }]) {
    await refresh(data);
    check('missing/unsupported quota data is not a fabricated number', label() === '(Remaining searches unavailable)');
  }
  await refresh(payload(3));
  await refresh({ detail: 'sensitive-upstream-error' }, 503);
  check('failure clears the old count and never displays upstream errors', label() === '(Remaining searches unavailable)');
  quotaReply = () => { throw new Error('network unavailable'); };
  await w.eval('refreshSuperHcQuota(true)');
  check('network failure is also unavailable', label() === '(Remaining searches unavailable)');
  quotaReply = () => ({ ok: true, status: 200, json: async () => { throw new Error('bad JSON'); } });
  await w.eval('refreshSuperHcQuota(true)');
  check('malformed JSON does not break the control', label() === '(Remaining searches unavailable)');

  await refresh(payload(3));
  w.document.getElementById('adv-super').click();
  check('Super HC still toggles normally without locally spending a search',
    w.eval('state.superHc') === true && label() === '(3 searches remaining for this month)');
  check('the account counter is never persisted with toggle preferences',
    !Object.hasOwn(JSON.parse(w.localStorage.getItem('st_adv')), 'remaining'));

  const beforeSearch = calls.length;
  quotaReply = () => reply(payload(2));
  const search = w.eval('runSearch()');
  check('starting a search does not decrement optimistically', label() === '(3 searches remaining for this month)');
  const stream = sources.at(-1);
  stream.emit('start', { live: true, providers: ['Flybasis'], query: { routes: [['CAI', 'LHR']], trip: 'oneway' } });
  stream.emit('complete', { live: true, providers: [], results: [], count: 0 });
  await search;
  await tick();
  check('completion fetches the new provider count', calls.length === beforeSearch + 1 && label() === '(2 searches remaining for this month)');
  w.eval('showSearchView()');
  check('returning to the search form shows the updated count', label() === '(2 searches remaining for this month)');

  quotaReply = () => reply(payload(1));
  const failedSearch = w.eval('runSearch()');
  sources.at(-1).emit('error', {});
  await failedSearch;
  await tick();
  check('failed search attempts also refresh rather than assuming no quota spent', label() === '(1 search remaining for this month)');

  let resolveOld;
  quotaReply = () => new Promise(resolve => { resolveOld = resolve; });
  const oldRequest = w.eval('refreshSuperHcQuota(true)');
  check('pending quota is explicitly loading', label() === '(Checking remaining searches…)');
  await refresh(payload(0));
  resolveOld(reply(payload(9)));
  await oldRequest;
  check('an older response cannot restore a spent allowance', label() === '(0 searches remaining for this month)');

  let resolveAfterLogout;
  quotaReply = () => new Promise(resolve => { resolveAfterLogout = resolve; });
  const pendingLogout = w.eval('refreshSuperHcQuota(true)');
  const beforeLogout = calls.length;
  w.eval('AUTH.user = null; syncAvatar();');
  resolveAfterLogout(reply(payload(8)));
  await pendingLogout;
  check('sign-out clears the count and rejects an in-flight response', label() === '(Sign in to see remaining searches)');
  check('signed-out page never calls the private quota endpoint', calls.length === beforeLogout);

  quotaReply = () => reply(payload(4));
  w.eval('AUTH.user = {email:"new@example.test", token:"new-synthetic-session"}; syncAvatar();');
  await tick();
  check('sign-in automatically fetches the current counter', label() === '(4 searches remaining for this month)');
  check('new session is used instead of a previous user credential', calls.at(-1).options.headers.Authorization === 'Bearer new-synthetic-session');

  await refresh({}, 401);
  check('expired auth has a sign-in state, not a zero allowance', label() === '(Sign in to see remaining searches)');
  check('a background quota check never redirects browsing to login', w.document.getElementById('view-login').hidden);

  await refresh(payload(4));
  quotaReply = () => reply(payload(5));
  w.eval('showSearchView(); superHcQuota.updatedAt = 0;');
  w.dispatchEvent(new w.Event('focus'));
  await tick();
  check('returning to the page refreshes stale counts/monthly resets', label() === '(5 searches remaining for this month)');
  check('no JavaScript errors', errors.length === 0);
  if (errors.length) console.log(errors);
} finally {
  w.close();
}
console.log(failures ? `\nsuper-hc-quota: ${failures} FAILED` : '\nsuper-hc-quota: PASS');
process.exitCode = failures ? 1 : 0;
