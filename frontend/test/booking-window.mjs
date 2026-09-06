// frontend/test/booking-window.mjs — regression test for the date picker's
// booking window: today through today + BOOK_WINDOW_DAYS is selectable,
// everything else is faded and disabled.
//
// Boots the REAL frontend/index.html in jsdom and drives the actual
// openDP()/drawDP() code — no reimplementation of the logic under test.
//
// Run:  cd frontend/test && npm install && node booking-window.mjs
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
    // The page fetches airports/programs on boot; none of that is under test.
    w.fetch = async () => ({ ok: false, status: 0, json: async () => ({}) });
  },
});
const w = dom.window;

let failures = 0;
const check = (label, cond, detail = '') => {
  if (cond) console.log(`  ok   ${label}${detail ? `  (${detail})` : ''}`);
  else { failures++; console.log(`  FAIL ${label}${detail ? `  (${detail})` : ''}`); }
};

setTimeout(() => {
  const d = w.document;

  const win = w.eval('BOOK_WINDOW_DAYS');
  const min = w.eval('bookableMin()');
  const max = w.eval('bookableMax()');
  const today = w.eval('todayISO()');
  console.log(`  window: ${min} .. ${max}  (${win} days from ${today})`);

  check('window is 330 days', win === 330, `${win}`);
  check('min is today', min === today, `${min} vs ${today}`);
  check('max is today+330', max === w.eval(`shiftISO(todayISO(), ${win})`), max);

  // --- the grid as drawn for the first month -----------------------------
  w.eval('openDP()');
  const cells = [...d.querySelectorAll('#dp [data-date]')];
  const enabled = cells.filter((b) => !b.disabled).map((b) => b.dataset.date);
  const disabled = cells.filter((b) => b.disabled).map((b) => b.dataset.date);
  check('grid rendered cells', cells.length > 0, `${cells.length}`);
  check('every enabled date is inside the window',
    enabled.every((x) => w.eval(`isBookable("${x}")`)));
  check('every disabled date is outside the window',
    disabled.every((x) => !w.eval(`isBookable("${x}")`)));
  check('disabled cells are faded (.off)',
    cells.filter((b) => b.disabled).every((b) => b.classList.contains('off')));
  check('disabled cells are aria-disabled',
    cells.filter((b) => b.disabled).every((b) => b.getAttribute('aria-disabled') === 'true'));

  // --- clicking ----------------------------------------------------------
  w.eval(`state.date = "${min}"; drawDP();`);
  const pastCell = d.querySelector(`#dp [data-date="${disabled.find((x) => x < today) || min}"]`);
  if (pastCell && pastCell.disabled) {
    const before = w.eval('state.date');
    pastCell.click();
    check('clicking a past date changes nothing', w.eval('state.date') === before,
      `${before} -> ${w.eval('state.date')}`);
  }
  const liveCell = [...d.querySelectorAll('#dp [data-date]')]
    .find((b) => !b.disabled && b.dataset.date !== w.eval('state.date'));
  if (liveCell) {
    liveCell.click();
    check('clicking a valid date selects it', w.eval('state.date') === liveCell.dataset.date,
      liveCell.dataset.date);
  }

  // --- upper boundary ----------------------------------------------------
  w.eval('dpMonth = monthOf(bookableMax()); drawDP();');
  const lastEnabled = [...d.querySelectorAll('#dp [data-date]')]
    .filter((b) => !b.disabled).map((b) => b.dataset.date).sort();
  check('last selectable day is exactly max', lastEnabled.at(-1) === max, `${lastEnabled.at(-1)} vs ${max}`);
  const dayAfter = w.eval('shiftISO(bookableMax(), 1)');
  const afterCell = d.querySelector(`#dp [data-date="${dayAfter}"]`);
  check('the day after max is disabled', !!afterCell && afterCell.disabled, dayAfter);
  check('next-month arrow disabled at the max month',
    !!d.querySelector('#dp [data-nav="1"]')?.disabled);

  // --- lower boundary ----------------------------------------------------
  w.eval('dpMonth = monthOf(bookableMin()); drawDP();');
  const firstEnabled = [...d.querySelectorAll('#dp [data-date]')]
    .filter((b) => !b.disabled).map((b) => b.dataset.date).sort();
  check('first selectable day is exactly today', firstEnabled[0] === min, `${firstEnabled[0]} vs ${min}`);
  check('prev-month arrow disabled at the min month',
    !!d.querySelector('#dp [data-nav="-1"]')?.disabled);

  // --- the ± flex window must not escape the booking window --------------
  w.eval('state.date = bookableMax(); state.flex = 2;');
  const flex = w.eval('flexDates()');
  check('±2 window at max stays inside the booking window',
    flex.length > 0 && flex.every((x) => w.eval(`isBookable("${x}")`)), JSON.stringify(flex));

  check('no JS errors while driving the picker', errors.length === 0,
    errors.slice(0, 2).join(' | '));

  console.log(failures === 0 ? '\nbooking-window: PASS' : `\nbooking-window: ${failures} FAILED`);
  process.exit(failures === 0 ? 0 : 1);
}, 2500);
