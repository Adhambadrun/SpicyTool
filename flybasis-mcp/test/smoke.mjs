// test/smoke.mjs — end-to-end smoke test against a locally-running
// local-server.js (which serves the exact same api/mcp.js Vercel handler).
// No mocks: initialize, tools/list, and a live tools/call all go over real
// HTTP/JSON-RPC to a real MCP server instance.
//
// The connector is KEYLESS (DuckDuckGo Lite + Wikipedia, self-hosted fetch),
// so live tools/call needs only outbound network. Where the sandbox has no
// outbound access, tools/call returns an upstream network error; the protocol
// surface (health, initialize, tools/list) is still verified and the data call
// is treated as a soft warning instead of a failure.
//
// Usage:
//   node local-server.js &            # in one terminal
//   node test/smoke.mjs               # in another

const BASE = process.env.SMOKE_BASE_URL || 'http://localhost:3900';

let idCounter = 1;
async function rpc(method, params) {
  const body = { jsonrpc: '2.0', id: idCounter++, method, params };
  const res = await fetch(`${BASE}/mcp`, {
    method: 'POST',
    headers: {
      'content-type': 'application/json',
      accept: 'application/json, text/event-stream',
    },
    body: JSON.stringify(body),
  });
  const text = await res.text();
  const contentType = res.headers.get('content-type') || '';
  if (contentType.includes('text/event-stream') || text.includes('\ndata:') || text.startsWith('data:')) {
    const dataLine = text.split('\n').find((l) => l.startsWith('data:'));
    if (!dataLine) throw new Error(`no SSE data line in response: ${text}`);
    return { status: res.status, json: JSON.parse(dataLine.slice(5).trim()) };
  }
  return { status: res.status, json: text ? JSON.parse(text) : null };
}

async function main() {
  console.log(`Smoke-testing flybasis-mcp at ${BASE} ...\n`);

  // 1. health
  const health = await fetch(`${BASE}/health`).then((r) => r.json());
  console.log('== GET /health ==');
  console.log(JSON.stringify(health, null, 2));
  if (!health.ok) throw new Error('health check failed');
  if (health.mode !== 'keyless') throw new Error(`expected keyless mode, got ${health.mode}`);

  // 2. initialize
  const init = await rpc('initialize', {
    protocolVersion: '2025-06-18',
    capabilities: {},
    clientInfo: { name: 'flybasis-mcp-smoke-test', version: '1.0.0' },
  });
  console.log('\n== initialize ==');
  console.log(`HTTP ${init.status}`);
  console.log(JSON.stringify(init.json, null, 2));
  if (init.status !== 200 || init.json?.error) throw new Error('initialize failed');
  const serverName = init.json?.result?.serverInfo?.name;
  if (serverName !== 'flybasis') throw new Error(`unexpected server name: ${serverName}`);

  // 3. tools/list
  const list = await rpc('tools/list', {});
  console.log('\n== tools/list ==');
  const tools = list.json?.result?.tools || [];
  console.log(`HTTP ${list.status}, ${tools.length} tools:`);
  for (const t of tools) {
    console.log(`  - ${t.name}: ${t.description.slice(0, 90)}${t.description.length > 90 ? '...' : ''}`);
  }
  if (tools.length < 1) throw new Error(`expected >= 1 tool, got ${tools.length}`);
  for (const name of ['web_search', 'instant_answer', 'fetch_url']) {
    if (!tools.find((t) => t.name === name)) throw new Error(`${name} tool missing`);
  }
  const searchTool = tools.find((t) => t.name === 'web_search');
  console.log('\nweb_search inputSchema:');
  console.log(JSON.stringify(searchTool.inputSchema, null, 2));

  // 4. tools/call instant_answer — keyless upstream; needs outbound network.
  const call = await rpc('tools/call', {
    name: 'instant_answer',
    arguments: { q: 'python programming language' },
  });
  console.log('\n== tools/call instant_answer {q:"python programming language"} ==');
  console.log(`HTTP ${call.status}`);
  const resultText = call.json?.result?.content?.[0]?.text;
  console.log(resultText);
  if (call.json?.result?.isError) {
    const upstreamUnavailable = /fetch failed|timed out|ENOTFOUND|ECONNRESET|network|photon|aggregate error|error:#/i.test(resultText || '');
    const providerUnavailable = /rate-limiting|all search providers failed/i.test(resultText || '');
    if (upstreamUnavailable) {
      console.log('\n[warn] outbound network unavailable in this sandbox — data calls need egress. Protocol surface verified; run in an environment with internet to exercise live data.');
    } else if (providerUnavailable) {
      console.log('\n[warn] upstream search provider rate-limited or unavailable right now (not a wiring bug).');
    } else {
      throw new Error(`tools/call returned unexpected isError: ${resultText}`);
    }
  } else {
    const parsed = resultText ? JSON.parse(resultText) : null;
    if (!parsed?.query) throw new Error('instant_answer returned an unexpected shape');
    if (!parsed?.meta?.provider) throw new Error('instant_answer meta missing');
  }

  console.log('\nAll smoke tests passed.');
}

main().catch((err) => {
  console.error('\nSMOKE TEST FAILED:', err);
  process.exit(1);
});
