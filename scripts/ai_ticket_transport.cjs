// Trusted coordinator transport. No raw command, target or path is accepted.
'use strict';
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const zlib = require('node:zlib');
const FIELDS = {
  list: [['operation'], ['after_id', 'limit']],
  claim: [['operation', 'ticket_id', 'version'], []],
  renew: [['operation', 'run_id', 'lease_token'], []],
  reply: [['operation', 'run_id', 'lease_token', 'payload'], []],
  defer: [['operation', 'run_id', 'lease_token', 'reason'], []],
  query_reserve: [['operation', 'run_id', 'lease_token', 'request_key', 'plan'], []],
  query_complete: [['operation', 'run_id', 'lease_token', 'query_id', 'result', 'error'], []],
};

function buildCommand(request) {
  if (!request || Array.isArray(request) || typeof request !== 'object' || typeof request.operation !== 'string' ||
      !Object.hasOwn(FIELDS, request.operation)) throw new Error('OPERATION_DENIED');
  const [required, optional] = FIELDS[request.operation];
  if (required.some(key => !Object.hasOwn(request, key)) ||
      Object.keys(request).some(key => !required.includes(key) && !optional.includes(key)))
    throw new Error('FIELDS_DENIED');
  const bytes = Buffer.from(JSON.stringify(request));
  if (bytes.length > 32000) throw new Error('INPUT_LIMIT');
  const encoded = bytes.toString('base64');
  return '/bin/bash -euo pipefail -c \'cd /opt/sapdesk; printf "%s" "' + encoded +
    '" | base64 --decode | docker compose --env-file .env.production exec -T api ' +
    'python manage.py ai_ticket_gateway | gzip -c | base64 -w0\'';
}

function clientFromCredentials() {
  const root = path.join(os.homedir(), '.codex/mcp/tencent-lighthouse');
  const file = path.join(os.homedir(), '.codex/security/sapdesk-ai-ticket/credentials.json');
  const descriptor = fs.openSync(file, fs.constants.O_RDONLY | fs.constants.O_NOFOLLOW);
  let auth;
  try {
    const stat = fs.fstatSync(descriptor);
    if (!stat.isFile() || stat.nlink !== 1 || stat.uid !== process.getuid() || (stat.mode & 0o777) !== 0o600)
      throw new Error('CREDENTIAL_PERMISSIONS');
    auth = JSON.parse(fs.readFileSync(descriptor, 'utf8'));
  } finally { fs.closeSync(descriptor); }
  const { AbstractClient } = require(path.join(root,
    'node_modules/tencentcloud-sdk-nodejs-common/tencentcloud/common/abstract_client.js'));
  return new AbstractClient('tat.tencentcloudapi.com', '2020-10-28', {
    credential: { secretId: auth.TENCENTCLOUD_SECRET_ID, secretKey: auth.TENCENTCLOUD_SECRET_KEY,
      token: auth.TENCENTCLOUD_TOKEN || undefined },
    region: 'na-siliconvalley', profile: { httpProfile: { reqTimeout: 20 } },
  });
}

async function invoke(request, client, sleep = ms => new Promise(resolve => setTimeout(resolve, ms))) {
  const command = buildCommand(request);
  const response = await client.request('RunCommand', {
    InstanceIds: ['lhins-9wkf7wc5'], CommandType: 'SHELL',
    Content: Buffer.from(command).toString('base64'), CommandName: 'sapdesk-ai-ticket-gateway',
    SaveCommand: false, Timeout: 60,
  });
  let taskId;
  for (let attempt = 0; attempt < 40; attempt++) {
    await sleep(1000);
    if (!taskId) {
      const invocations = await client.request('DescribeInvocations', { InvocationIds: [response.InvocationId] });
      taskId = invocations.InvocationSet?.[0]?.InvocationTaskBasicInfoSet?.[0]?.InvocationTaskId;
      if (!taskId) continue;
    }
    const tasks = await client.request('DescribeInvocationTasks', { InvocationTaskIds: [taskId], HideOutput: false });
    const task = tasks.InvocationTaskSet?.[0];
    if (!task || ['PENDING', 'RUNNING'].includes(task.TaskStatus)) continue;
    if (task.TaskStatus !== 'SUCCESS' || task.TaskResult?.ExitCode !== 0 || task.TaskResult?.OutputTruncated)
      throw new Error('GATEWAY_FAILED');
    const encoded = Buffer.from(task.TaskResult.Output || '', 'base64').toString('ascii').trim();
    if (!encoded || !/^[A-Za-z0-9+/=]+$/.test(encoded)) throw new Error('OUTPUT_INVALID');
    const raw = zlib.gunzipSync(Buffer.from(encoded, 'base64'), { maxOutputLength: 1_000_000 });
    const result = JSON.parse(raw.toString('utf8'));
    if (!result || Array.isArray(result) || typeof result !== 'object') throw new Error('OUTPUT_INVALID');
    return result;
  }
  throw new Error('GATEWAY_TIMEOUT');
}

module.exports = { buildCommand, invoke };
if (require.main === module) {
  Promise.resolve().then(async () => {
    const raw = fs.readFileSync(0);
    if (raw.length > 32000) throw new Error('INPUT_LIMIT');
    const request = JSON.parse(raw.toString('utf8'));
    buildCommand(request);
    const result = await invoke(request, clientFromCredentials());
    process.stdout.write(JSON.stringify(result) + '\n');
  }).catch(() => {
    process.stderr.write('AI_TICKET_TRANSPORT_FAILED\n');
    process.exitCode = 1;
  });
}
