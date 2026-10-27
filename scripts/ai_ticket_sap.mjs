// Trusted company-tool adapter. This module is never a model tool.
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { spawnSync } from 'node:child_process';

try {
  const input = fs.readFileSync(0);
  if (input.length > 32000) throw new Error('invalid request');
  const request = JSON.parse(input.toString('utf8'));
  if (Object.keys(request).sort().join(',') !== 'approved_user,args,workspace' ||
      typeof request.workspace !== 'string' || !path.isAbsolute(request.workspace) ||
      !Array.isArray(request.args) || request.args.some(value => typeof value !== 'string'))
    throw new Error('invalid request');
  const root = fs.realpathSync(request.workspace);
  const tools = path.join(root, 'SAP 万能工具箱/scripts/sap-eyes');
  const security = path.join(root, 'local-sap-security');
  const cli = path.join(tools, 'cli.mjs');
  const processArgv = args => [process.execPath, cli, ...args];
  const { parseCliArgs, validateCliPolicy } = await import(pathToFileURL(path.join(tools, 'policy.mjs')));
  let args = request.args;
  if (!['ddic', 'data', 'source', 'tcode'].includes(args[0]) || !args.includes('--conn') ||
      args.some(arg => ['--approval-token', '--approval-request-output', '--allow-prod', '--approved-user'].includes(arg)))
    throw new Error('operation denied');
  const production = args[args.indexOf('--conn') + 1] === 'PS4';
  let folder;
  try {
    if (production) {
      if (typeof request.approved_user !== 'string' || !/^[A-Z0-9_-]{2,32}$/.test(request.approved_user))
        throw new Error('approved user missing');
      args = [...args, '--allow-prod', '--approved-user', request.approved_user];
      folder = fs.mkdtempSync(path.join(fs.realpathSync(os.tmpdir()), 'sapdesk-ticket-approval-'));
      const tokenFile = path.join(folder, 'token.json');
      const policy = validateCliPolicy(parseCliArgs(processArgv([...args, '--approval-token', tokenFile])));
      if (policy.showHelp || !policy.approvalScope) throw new Error('approval scope is missing');
      const { canonicalizeJson, hashApprovalScope } = await import(pathToFileURL(path.join(security, 'signed-approval.mjs')));
      const { signApprovalRequest } = await import(pathToFileURL(path.join(security, 'local-approval-signer.mjs')));
      const requestFile = path.join(folder, 'request.json');
      fs.writeFileSync(requestFile, canonicalizeJson({ schema: 'sap-approval-request/v1',
        scope: policy.approvalScope, scopeHash: hashApprovalScope(policy.approvalScope) }), { mode: 0o600, flag: 'wx' });
      signApprovalRequest({ requestPath: requestFile, outputPath: tokenFile });
      args = [...args, '--approval-token', tokenFile];
    } else {
      if (validateCliPolicy(parseCliArgs(processArgv(args))).showHelp) throw new Error('read command is missing');
    }
    const result = spawnSync(process.execPath, [cli, ...args], {
      cwd: root, encoding: 'utf8', timeout: 45000, maxBuffer: 1_000_000,
      env: { PATH: process.env.PATH, HOME: os.homedir(), LANG: 'zh_CN.UTF-8' },
    });
    if (result.error || result.status !== 0) throw new Error(result.stderr || 'SAP read failed');
    if (Buffer.byteLength(result.stdout) > 64 * 1024) throw new Error('evidence size limit');
    const envelope = JSON.parse(result.stdout);
    if (envelope?._evidence?.trust !== 'UNTRUSTED_EVIDENCE') throw new Error('evidence invalid');
    process.stdout.write(JSON.stringify(envelope) + '\n');
  } finally {
    if (folder) fs.rmSync(folder, { recursive: true, force: true });
  }
} catch (error) {
  const message = String(error?.message || '');
  const reason = /approval|signature|token|trust|profile|identity|credential|approved user/i.test(message)
    ? 'authorization_missing' : /timeout|timed out/i.test(message) ? 'time_limit'
    : /evidence/i.test(message) ? 'evidence_incomplete' : 'sap_unavailable';
  process.stdout.write(JSON.stringify({ error: reason }) + '\n');
}
