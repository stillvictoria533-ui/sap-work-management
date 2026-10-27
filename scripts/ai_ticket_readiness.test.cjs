const { test } = require('node:test');
const assert = require('node:assert/strict');
const { collectReadiness, heartbeatPayload, proxyReady } = require('./ai_ticket_readiness.cjs');

test('only bounded boolean readiness reaches the trusted heartbeat', async () => {
  const env = { AI_TICKET_SAP_WORKSPACE: '/company', AI_TICKET_PS4_APPROVED_USER: 'REVIEW_USER',
    https_proxy: 'http://127.0.0.1:7897', TENCENTCLOUD_SECRET_KEY: 'never-export-this' };
  const flags = await collectReadiness(env, { home: '/home/example', isExecutable: () => true,
    isFile: () => true, probe: async (host, port) => host === '127.0.0.1' && port === 7897 });
  assert.deepEqual(flags, { model_cli: true, sap_tools: true, production_identity: true, proxy: true });
  const json = Buffer.from(heartbeatPayload(flags), 'base64').toString('utf8');
  assert.deepEqual(JSON.parse(json), flags);
  assert.ok(!json.includes('never-export-this'));
  assert.ok(!json.includes('REVIEW_USER'));
  assert.throws(() => heartbeatPayload({ ...flags, extra: 'instruction' }));
});

test('proxy probing is restricted to loopback and detects unavailable dependencies', async () => {
  let probes = 0;
  assert.equal(await proxyReady('https://user:secret@example.com:443', async () => { probes++; return true; }), false);
  assert.equal(probes, 0);
  assert.equal(await proxyReady('socks5://127.0.0.1', async (host, port) => host === '127.0.0.1' && port === 1080), true);
  const flags = await collectReadiness({ AI_TICKET_SAP_WORKSPACE: '/missing', https_proxy: 'http://localhost:7897' },
    { isExecutable: () => false, isFile: () => false, probe: async () => false });
  assert.deepEqual(flags, { model_cli: false, sap_tools: false, production_identity: false, proxy: false });
});
