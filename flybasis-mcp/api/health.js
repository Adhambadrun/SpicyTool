// api/health.js — unauthenticated health check.
import { BASE_URL_FOR_HEALTH, TOOL_MODE, CONFIGURED_PROVIDERS } from '../lib/tools.js';

export default function handler(req, res) {
  res.status(200).json({
    ok: true,
    service: 'flybasis-mcp',
    version: '2.0.0',
    mode: TOOL_MODE,
    providers: CONFIGURED_PROVIDERS,
    upstream: BASE_URL_FOR_HEALTH,
    keyless: TOOL_MODE === 'keyless',
  });
}
