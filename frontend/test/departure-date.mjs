// Regression for the reported Sep 7 departure that would not change in a
// round-trip picker. Exercise the actual DOM handlers, not a duplicate model.
import assert from 'node:assert/strict';
import fs from 'node:fs';
import { JSDOM, VirtualConsole } from 'jsdom';

const html = fs.readFileSync(new URL('../index.html', import.meta.url), 'utf8');
const errors = [];
const consoleProxy = new VirtualConsole();
consoleProxy.on('jsdomError', error => errors.push(error.message));
consoleProxy.on('error', (...args) => errors.push(args.join(' ')));
const dom = new JSDOM(html, {
  url: 'https://spicytool.test/', runScripts: 'dangerously', pretendToBeVisual: true,
  virtualConsole: consoleProxy,
  beforeParse(w) {
    const NativeDate = w.Date;
    const now = new NativeDate('2026-09-07T12:00:00').getTime();
    w.Date = class extends NativeDate {
      constructor(...args) { super(...(args.length ? args : [now])); }
      static now() { return now; }
    };
    w.fetch = async url => ({ ok: true, status: 200, json: async () =>
      url === '/api/v1/programs' ? { programs: [] } : [] });
    w.scrollTo = () => {};
  },
});
const w = dom.window, d = w.document;
await new Promise(resolve => setTimeout(resolve, 0));
let searches = 0;
w.startSearch = () => { searches++; }; // no accidental real search on Enter
const value = name => w.eval(`state.${name}`);
const click = selector => {
  const node = d.querySelector(selector);
  assert.ok(node, `Missing calendar control: ${selector}`);
  node.click();
};
const pick = date => click(`#dp [data-date="${date}"]`);
const reset = (overrides = {}) => {
  w.eval(`closeDP(); Object.assign(state, ${JSON.stringify({
    trip: 'roundtrip', date: '2026-09-07', returnDate: null, flex: 0, retFlex: 0, ...overrides,
  })}); syncDate();`);
  searches = 0;
};
const open = () => click('#f-date');
const keyboardActivate = (node, key = 'Enter') => {
  assert.ok(node, 'Missing keyboard-operable calendar control');
  node.focus();
  const event = new w.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true });
  node.dispatchEvent(event);
  // jsdom does not synthesize native button activation. A browser only clicks
  // if the key's default action was not cancelled and the button still exists.
  if (!event.defaultPrevented && node.isConnected && node.tagName === 'BUTTON') node.click();
  return event;
};
let failures = 0;
function test(name, fn) {
  try { reset(); fn(); console.log(`  ok   ${name}`); }
  catch (error) { failures++; console.log(`  FAIL ${name}: ${error.message}`); }
}

test('round-trip calendar opens on Departure, visibly active', () => {
  open();
  assert.equal(w.eval('dpArm'), 'dep');
  assert.ok(d.querySelector('#dp .dp-row[data-arm="dep"]').classList.contains('arm'));
  assert.equal(d.querySelector('#dp button[data-arm="dep"]').getAttribute('aria-pressed'), 'true');
});
test('first later-day click changes departure instead of creating a return', () => {
  open(); pick('2026-09-12');
  assert.equal(value('date'), '2026-09-12');
  assert.equal(value('returnDate'), null);
  assert.equal(d.getElementById('date-val').textContent, 'Sep 12 → Add return');
  assert.equal(w.eval('dpArm'), 'ret');
  assert.ok(d.querySelector('#dp .dp-row[data-arm="ret"]').classList.contains('arm'));
});
test('next day click sets the return without overwriting departure', () => {
  open(); pick('2026-09-12'); pick('2026-09-20');
  assert.equal(value('date'), '2026-09-12');
  assert.equal(value('returnDate'), '2026-09-20');
});
test('the Round Trip tab also starts with departure selection', () => {
  reset({ trip: 'oneway' });
  click('#tab-round'); pick('2026-09-15');
  assert.equal(value('date'), '2026-09-15');
  assert.equal(value('returnDate'), null);
});
test('explicit departure editing keeps a still-valid return', () => {
  reset({ date: '2026-09-12', returnDate: '2026-09-20' });
  open(); click('#dp .dp-row[data-arm="dep"]'); pick('2026-09-15');
  assert.equal(value('date'), '2026-09-15');
  assert.equal(value('returnDate'), '2026-09-20');
});
test('moving departure beyond the return clears the invalid return', () => {
  reset({ date: '2026-09-12', returnDate: '2026-09-20' });
  open(); pick('2026-09-24');
  assert.equal(value('date'), '2026-09-24');
  assert.equal(value('returnDate'), null);
});
test('clearing departure arms Departure so the replacement date is editable', () => {
  reset({ date: '2026-09-12', returnDate: '2026-09-20', retFlex: 2 });
  open(); click('#dp [data-clear="dep"]'); pick('2026-09-17');
  assert.equal(value('date'), '2026-09-17');
  assert.equal(value('returnDate'), null);
  assert.equal(value('retFlex'), 0);
});
test('clearing a distant departure brings its reset month into view', () => {
  reset({ date: '2026-12-12', returnDate: '2026-12-20' });
  open(); click('#dp [data-clear="dep"]');
  assert.equal(w.eval('dpArm'), 'dep');
  assert.equal(d.querySelector('#dp .dp-month').textContent, 'Sep 2026');
  pick('2026-09-17');
  assert.equal(value('date'), '2026-09-17');
});
test('clearing return continues to edit Return, not Departure', () => {
  reset({ date: '2026-09-12', returnDate: '2026-09-20', retFlex: 2 });
  open(); click('#dp [data-clear="ret"]'); pick('2026-09-23');
  assert.equal(value('date'), '2026-09-12');
  assert.equal(value('returnDate'), '2026-09-23');
});
test('month navigation preserves the departure target and leaves the picker open', () => {
  open(); click('#dp [data-nav="1"]'); pick('2026-10-08');
  assert.equal(value('date'), '2026-10-08');
  assert.equal(value('returnDate'), null);
  assert.ok(d.getElementById('dp'));
});
test('the January grid beside December uses real next-year dates', () => {
  open();
  for (let i = 0; i < 3; i++) click('#dp [data-nav="1"]');
  assert.equal(d.querySelectorAll('#dp .dp-month')[1].textContent, 'Jan 2027');
  assert.equal(d.querySelector('#dp [data-date^="2026-13"]'), null);
  pick('2027-01-05');
  assert.equal(value('date'), '2027-01-05');
  assert.equal(value('returnDate'), null);
});
test('one-way departure can be changed repeatedly', () => {
  reset({ trip: 'oneway' }); open(); pick('2026-09-10'); pick('2026-09-18');
  assert.equal(value('date'), '2026-09-18');
  assert.equal(value('returnDate'), null);
  assert.equal(w.eval('dpArm'), 'dep');
});
test('Cancel restores both dates and their flexibility', () => {
  reset({ date: '2026-09-10', returnDate: '2026-09-20', flex: 1, retFlex: 2 });
  const originalLabel = d.getElementById('date-val').textContent;
  open(); pick('2026-09-12'); pick('2026-09-25');
  click('#dp [data-flex="2"]'); click('#dp [data-ret="1"]'); click('#dp [data-dp="cancel"]');
  assert.deepEqual([value('date'), value('returnDate'), value('flex'), value('retFlex')], ['2026-09-10', '2026-09-20', 1, 2]);
  assert.equal(d.getElementById('date-val').textContent, originalLabel);
  assert.equal(d.getElementById('dp'), null);
});
test('Set dates keeps the edited departure, and reopening edits departure first', () => {
  open(); pick('2026-09-12'); pick('2026-09-20'); click('#dp [data-dp="save"]');
  assert.equal(d.getElementById('dp'), null);
  assert.equal(d.getElementById('date-val').textContent, 'Sep 12 → Sep 20');
  open(); pick('2026-09-15');
  assert.equal(value('date'), '2026-09-15');
  assert.equal(value('returnDate'), '2026-09-20');
});
test('Enter on the outer date field opens the calendar, not a flight search', () => {
  keyboardActivate(d.getElementById('f-date'));
  assert.ok(d.getElementById('dp'));
  assert.equal(searches, 0);
  assert.equal(w.eval('dpArm'), 'dep');
});
test('Enter/Space inside the calendar select dates without reopening or searching', () => {
  open();
  const popup = d.getElementById('dp');
  keyboardActivate(d.querySelector('#dp [data-date="2026-09-12"]'));
  assert.equal(d.getElementById('dp'), popup);
  assert.equal(value('date'), '2026-09-12');
  keyboardActivate(d.querySelector('#dp [data-date="2026-09-20"]'), ' ');
  assert.equal(value('returnDate'), '2026-09-20');
  assert.equal(searches, 0);
  assert.equal(d.activeElement.dataset.date, '2026-09-20');
});
test('date-arm controls and Set dates work from the keyboard', () => {
  open(); pick('2026-09-12'); pick('2026-09-20');
  keyboardActivate(d.querySelector('#dp button[data-arm="dep"]'));
  keyboardActivate(d.querySelector('#dp [data-date="2026-09-15"]'));
  keyboardActivate(d.querySelector('#dp [data-dp="save"]'));
  assert.equal(value('date'), '2026-09-15');
  assert.equal(value('returnDate'), '2026-09-20');
  assert.equal(d.getElementById('dp'), null);
  assert.equal(searches, 0);
});
test('keyboard month navigation does not reset the Cancel snapshot', () => {
  open(); pick('2026-09-12');
  keyboardActivate(d.querySelector('#dp [data-nav="1"]'));
  assert.equal(d.querySelector('#dp .dp-month').textContent, 'Oct 2026');
  keyboardActivate(d.querySelector('#dp [data-dp="cancel"]'));
  assert.equal(value('date'), '2026-09-07');
  assert.equal(value('returnDate'), null);
  assert.equal(searches, 0);
});
test('past dates remain disabled', () => {
  open();
  const past = d.querySelector('#dp [data-date="2026-09-06"]');
  assert.ok(past.disabled);
  past.click();
  assert.equal(value('date'), '2026-09-07');
});
test('calendar interactions produce no JavaScript errors', () => {
  assert.deepEqual(errors, []);
});

w.close();
console.log(failures ? `\ndeparture-date: ${failures} FAILED` : '\ndeparture-date: PASS');
process.exitCode = failures ? 1 : 0;
