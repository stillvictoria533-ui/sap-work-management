// Deterministic queue probe. Only READY/EMPTY/DISABLED reaches stdout.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { AbstractClient } = require(path.join(
  os.homedir(), '.codex/mcp/tencent-lighthouse/node_modules/tencentcloud-sdk-nodejs-common/tencentcloud/common/abstract_client.js',
));

const credentialsPath = path.join(os.homedir(), '.codex/security/sapdesk-ai-ticket/credentials.json');
if (fs.statSync(credentialsPath).mode & 0o077) throw new Error('CREDENTIAL_PERMISSIONS');
const auth = JSON.parse(fs.readFileSync(credentialsPath, 'utf8'));
const client = new AbstractClient('tat.tencentcloudapi.com', '2020-10-28', {
  credential: {
    secretId: auth.TENCENTCLOUD_SECRET_ID,
    secretKey: auth.TENCENTCLOUD_SECRET_KEY,
    token: auth.TENCENTCLOUD_TOKEN || undefined,
  },
  region: 'na-siliconvalley',
  profile: { httpProfile: { reqTimeout: 30 } },
});
const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
const scanCode = [
  'import hashlib',
  'from desk.ai import candidate_page',
  'after=0',
  'for _ in range(100):',
  '    page=candidate_page(after=after,limit=50)',
  '    if not page["enabled"]: print("DISABLED"); break',
  '    if page["results"]:',
  '        markers="|".join(str(x["id"])+":"+str(x["version"])+":"+str(x["trigger_event_id"]) for x in page["results"])',
  '        print("READY:"+hashlib.sha256(markers.encode()).hexdigest()[:16]); break',
  '    if not page["has_more"]: print("EMPTY"); break',
  '    after=page["next_after_id"]',
  'else: print("SCAN_LIMIT")',
].join('\n');
const remoteCommand =
  "cd /opt/sapdesk && docker compose --env-file .env.production exec -T api " +
  "python manage.py shell --no-imports -c '" + scanCode + "'";

async function scan() {
  const request = await client.request('RunCommand', {
    InstanceIds: ['lhins-9wkf7wc5'], CommandType: 'SHELL',
    Content: Buffer.from(remoteCommand).toString('base64'),
    CommandName: 'sapdesk-ai-ticket-scan', SaveCommand: false, Timeout: 60,
  });
  let taskId;
  for (let attempt = 0; attempt < 45; attempt++) {
    await sleep(1500);
    if (!taskId) {
      const invocation = await client.request('DescribeInvocations', { InvocationIds: [request.InvocationId] });
      taskId = invocation.InvocationSet?.[0]?.InvocationTaskBasicInfoSet?.[0]?.InvocationTaskId;
      if (!taskId) continue;
    }
    const response = await client.request('DescribeInvocationTasks', {
      InvocationTaskIds: [taskId], HideOutput: false,
    });
    const task = response.InvocationTaskSet?.[0];
    if (!task || ['PENDING', 'RUNNING'].includes(task.TaskStatus)) continue;
    if (task.TaskStatus !== 'SUCCESS' || task.TaskResult?.ExitCode !== 0) throw new Error('REMOTE_SCAN_FAILED');
    const lines = Buffer.from(task.TaskResult?.Output || '', 'base64').toString('utf8').trim().split(/\r?\n/);
    const status = lines.at(-1);
    if (!['EMPTY', 'DISABLED'].includes(status) && !/^READY:[0-9a-f]{16}$/.test(status))
      throw new Error('SCAN_RESULT_INVALID');
    process.stdout.write(status + '\n');
    return;
  }
  throw new Error('SCAN_TIMEOUT');
}

scan().catch(error => {
  // Never print remote output, business text or credentials from this process.
  const allowed = new Set(['CREDENTIAL_PERMISSIONS', 'REMOTE_SCAN_FAILED', 'SCAN_RESULT_INVALID', 'SCAN_TIMEOUT']);
  const code = typeof error.code === 'string' && /^[A-Za-z0-9_.-]{1,60}$/.test(error.code)
    ? error.code : 'SCAN_TRANSPORT_FAILED';
  process.stderr.write((allowed.has(error.message) ? error.message : code) + '\n');
  process.exitCode = 1;
});
