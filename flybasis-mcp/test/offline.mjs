// test/offline.mjs — ZERO-NETWORK unit tests for the keyless connector logic.
// Verifies the pure parsers/normalizers in lib/tools.js against fixture HTML/JSON
// so the connector can be validated in any sandbox with no outbound access.
//
// Usage: npm run test:offline   (or node test/offline.mjs)

import assert from 'node:assert/strict';
import {
  PURE,
  parseDdgLiteHtml,
  htmlToText,
  htmlToMarkdown,
  resolveDdgHref,
  assertPublicUrl,
  domainOf,
} from '../lib/tools.js';

let passed = 0;
function ok(name, fn) {
  fn();
  passed += 1;
  console.log(`  ok - ${name}`);
}
function fail(name, err) {
  console.error(`  FAIL - ${name}: ${err.message}`);
  process.exitCode = 1;
}

console.log('offline tests (no network)\n');

ok('PURE exports exist', () => {
  assert.equal(typeof PURE.parseDdgLiteHtml, 'function');
  assert.equal(typeof PURE.htmlToText, 'function');
  assert.equal(typeof PURE.htmlToMarkdown, 'function');
  assert.equal(typeof PURE.assertPublicUrl, 'function');
  assert.equal(typeof PURE.resolveDdgHref, 'function');
});

ok('parseDdgLiteHtml extracts organic results + skips ads', () => {
  const fixture = `
  <html><body>
    <div class="zero-click"><a href="https://duckduckgo.com/Python_(programming_language)"><b>Python (programming language)</b></a> A high-level language.</div>
    <table>
      <tr><td><a rel="nofollow" class="result-link" href="https://duckduckgo.com/y.js?ad_domain=skillshare.com">Learn Python</a></td><td class="result-snippet">Paid course</td></tr>
      <tr><td><a rel="nofollow" class="result-link" href="https://www.python.org/">Welcome to Python.org</a></td><td class="result-snippet">The official home of the Python programming language.</td></tr>
      <tr><td><a rel="nofollow" class="result-link" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fen.wikipedia.org%2Fwiki%2FPython_(programming_language)">Python - Wikipedia</a></td><td class="result-snippet">A high-level language &amp; more.</td></tr>
    </table>
  </body></html>`;
  const out = parseDdgLiteHtml(fixture, { limit: 10 });
  assert.equal(out.results.length, 2, 'ads are filtered');
  assert.equal(out.adsFiltered, 1);
  assert.equal(out.results[0].title, 'Welcome to Python.org');
  assert.equal(out.results[0].domain, 'python.org');
  assert.equal(out.results[0].snippet, 'The official home of the Python programming language.');
  assert.equal(out.results[1].url, 'https://en.wikipedia.org/wiki/Python_(programming_language)');
  assert.equal(out.zeroClick.heading, 'Python (programming language)');
});

ok('parseDdgLiteHtml detects the rate-limit challenge', () => {
  const challenge = '<html><fieldset><legend>checking</legend></fieldset><div class="challenge">Select all squares containing a duck</div></html>';
  assert.throws(() => parseDdgLiteHtml(challenge), /rate-limiting/i);
});

ok('resolveDdgHref decodes uddg redirects', () => {
  assert.equal(
    resolveDdgHref('https://duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa%3Fb%3D1'),
    'https://example.com/a?b=1'
  );
  assert.equal(resolveDdgHref('https://example.com/x'), 'https://example.com/x');
});

ok('htmlToText strips boilerplate and keeps prose', () => {
  const html = `<html><head><title>T</title><style>.x{}</style><script>bad()</script></head>
  <body><nav>menu stuff</nav><main><h1>Hello</h1><p>This is &amp; a <b>test</b> page.</p><footer>footer junk</footer></main></body></html>`;
  const text = htmlToText(html);
  assert.ok(text.includes('Hello'));
  assert.ok(text.includes('This is & a test page.'));
  assert.ok(!text.includes('menu stuff'));
  assert.ok(!text.includes('footer junk'));
  assert.ok(!text.includes('bad()'));
});

ok('htmlToMarkdown emits headings and links', () => {
  const md = htmlToMarkdown('<h1>Title</h1><p>Read <a href="/docs">the docs</a>.</p>', 'https://example.com');
  assert.ok(md.includes('# Title'));
  assert.ok(md.includes('[the docs](https://example.com/docs)'));
});

ok('assertPublicUrl blocks private/loopback targets', () => {
  for (const bad of [
    'http://localhost:8000/',
    'http://127.0.0.1:8000/',
    'http://10.0.0.1/',
    'http://192.168.1.1/',
    'http://169.254.169.254/latest/meta-data/',
    'http://172.16.0.1/',
    'file:///etc/passwd',
    'ftp://example.com/',
  ]) {
    assert.throws(() => assertPublicUrl(bad), new RegExp('allowed|not allowed|Private|Reserved', 'i'), bad);
  }
  assert.doesNotThrow(() => assertPublicUrl('https://example.com/path'));
});

ok('domainOf extracts bare hostname', () => {
  assert.equal(domainOf('https://www.python.org/docs/'), 'python.org');
});

if (!process.exitCode) {
  console.log(`\nAll ${passed} offline tests passed.`);
} else {
  console.log(`\nFAILURES — ${passed} passed.`);
}
