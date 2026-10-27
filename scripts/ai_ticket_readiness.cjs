'use strict';
const fs = require('node:fs');
const net = require('node:net');
const os = require('node:os');
const path = require('node:path');

const KEYS = ['model_cli', 'sap_tools', 'production_identity', 'proxy'];
const CLI = '.codex/runtime/codex-cli-0.162.1/node_modules/.bin/codex';
const SAP_FILES = ['SAP 万能工具箱/scripts/sap-eyes/cli.mjs', 'SAP 万能工具箱/scripts/sap-eyes/policy.mjs',
  'local-sap-security/local-approval-signer.mjs', 'local-sap-security/signed-approval.mjs'];

function fileExists(file) {
  try { return fs.statSync(file).isFile(); } catch { return false; }
}
function executable(file) {
  try { fs.accessSync(file, fs.constants.X_OK); return fileExists(file); } catch { return false; }
}
function probeTcp(host, port) {
  return new Promise(resolve => {
    let socket;
    let finished = false;
    const done = value => {
      if (finished) return;
      finished = true;
      if (socket) socket.destroy();
      resolve(value);
    };
    try {
      socket = net.createConnection({ host, port });
      socket.setTimeout(800);
      socket.once('connect', () => done(true));
      socket.once('timeout', () => done(false));
      socket.once('error', () => done(false));
    } catch { done(false); }
  });
}
async function proxyReady(raw, probe) {
  try {
    const url = new URL(raw || '');
    const hostname = url.hostname.replace(/^\[|\]$/g, '');
    if (!['http:', 'https:', 'socks:', 'socks5:', 'socks5h:'].includes(url.protocol) ||
        !['127.0.0.1', 'localhost', '::1'].includes(hostname) || url.username || url.password) return false;
    const port = Number(url.port || (url.protocol === 'https:' ? 443 : url.protocol.startsWith('socks') ? 1080 : 80));
    if (!Number.isInteger(port) || port < 1 || port > 65535) return false;
    for (const host of hostname === 'localhost' ? ['127.0.0.1', '::1'] : [hostname]) {
      if (await probe(host, port)) return true;
    }
    return false;
  } catch { return false; }
}
async function collectReadiness(env = process.env, options = {}) {
  const home = options.home || os.homedir();
  const isExecutable = options.isExecutable || executable;
  const isFile = options.isFile || fileExists;
  const probe = options.probe || probeTcp;
  const workspace = env.AI_TICKET_SAP_WORKSPACE || '';
  return {
    model_cli: isExecutable(path.join(home, CLI)),
    sap_tools: path.isAbsolute(workspace) && SAP_FILES.every(file => isFile(path.join(workspace, file))),
    production_identity: /^[A-Z0-9_-]{2,32}$/.test(env.AI_TICKET_PS4_APPROVED_USER || ''),
    proxy: await proxyReady(env.https_proxy || env.HTTPS_PROXY, probe),
  };
}
function heartbeatPayload(capabilities) {
  if (!capabilities || Object.keys(capabilities).sort().join(',') !== [...KEYS].sort().join(',') ||
      KEYS.some(key => typeof capabilities[key] !== 'boolean')) throw new Error('INVALID_HEALTH');
  return Buffer.from(JSON.stringify(capabilities)).toString('base64');
}
module.exports = { collectReadiness, heartbeatPayload, proxyReady };
