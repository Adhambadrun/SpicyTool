// api/mcp.js — the MCP endpoint (stateless streamable-HTTP).
//
// This connector is self-contained and KEYLESS: web search, instant answers and
// URL-to-text fetching are implemented inside this repo (lib/tools.js) against
// public, keyless sources (DuckDuckGo + Wikipedia) with an optional
// operator-supplied Brave/Serper key. No RapidAPI, no proxy secret, no external
// upstream deployment is required. See README.md.
//
// No MCP-caller auth, no sessions, no Supabase: every caller gets the same
// live web data. A per-IP soft rate limit (lib/ratelimit.js) protects the
// connector from accidental bursts.

import { McpServer } from '@modelcontextprotocol/sdk/server/mcp.js';
import { StreamableHTTPServerTransport } from '@modelcontextprotocol/sdk/server/streamableHttp.js';
import { registerTools } from '../lib/tools.js';
import { clientIp, checkAndConsume } from '../lib/ratelimit.js';

export const VERSION = '2.0.0';

export default async function handler(req, res) {
  // Streamable-HTTP stateless servers only accept POST; GET/SSE-resume and
  // DELETE/session-teardown don't apply since there is no session state.
  if (req.method === 'GET' || req.method === 'DELETE') {
    return res.status(405).json({
      jsonrpc: '2.0',
      error: { code: -32000, message: 'Method not allowed (stateless server)' },
      id: null,
    });
  }
  if (req.method !== 'POST') {
    return res.status(405).json({
      jsonrpc: '2.0',
      error: { code: -32000, message: 'Method not allowed' },
      id: null,
    });
  }

  // tools/call is the only expensive operation (it hits search/fetch upstreams);
  // initialize/tools/list are free protocol handshakes.
  if (req.body?.method === 'tools/call') {
    const { allowed, limit } = checkAndConsume(clientIp(req));
    if (!allowed) {
      return res.status(200).json({
        jsonrpc: '2.0',
        id: req.body?.id ?? null,
        result: {
          isError: true,
          content: [{
            type: 'text',
            text: `Rate limit exceeded (${limit} tool calls/hour on this connector). Configure FLYBASIS_MCP_RATE_LIMIT on the server to raise it.`,
          }],
        },
      });
    }
  }

  const server = new McpServer({ name: 'flybasis', version: VERSION });
  registerTools(server);

  const transport = new StreamableHTTPServerTransport({ sessionIdGenerator: undefined });
  res.on('close', () => {
    transport.close();
    server.close();
  });

  try {
    await server.connect(transport);
    await transport.handleRequest(req, res, req.body);
  } catch (err) {
    console.error('[mcp] error:', err);
    if (!res.headersSent) {
      res.status(500).json({ jsonrpc: '2.0', error: { code: -32603, message: 'Internal server error' }, id: null });
    }
  }
}
