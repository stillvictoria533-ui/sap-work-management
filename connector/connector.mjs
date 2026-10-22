#!/usr/bin/env node
// Portable Mac pilot. Device tokens stay in memory. SAP secrets never enter this process.
import { spawn } from 'node:child_process';
import { createHash } from 'node:crypto';
import { createInterface } from 'node:readline/promises';
import { existsSync } from 'node:fs';
import { homedir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { setTimeout as delay } from 'node:timers/promises';

export const SAP_CLI = path.join(homedir(), 'Desktop/SAP项目独立工作区/SAP 万能工具箱/scripts/sap-eyes/cli.mjs');
export const MAX_BYTES = 96 * 1024;
const JOB_KEYS = ['id', 'system', 'client', 'operation', 'object_name', 'expires_at', 'lease'];
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export function serverOrigin(value) {
  const url = new URL(value);
  if (url.username || url.password || url.pathname !== '/' || url.search || url.hash ||
      (url.protocol !== 'https:' && !(url.protocol === 'http:' && ['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)))) {
    throw new Error('服务器只允许 HTTPS；本地预览可使用回环 HTTP 地址。');
  }
  return url.origin;
}

export function jobArguments(job, now = Date.now()) {
  if (!job || typeof job !== 'object' || Array.isArray(job) || Object.keys(job).sort().join() !== [...JOB_KEYS].sort().join() ||
      !UUID.test(job.id) || job.system !== 'DS4' || job.client !== '110' ||
      !['tcode', 'ddic'].includes(job.operation) || typeof job.object_name !== 'string' ||
      !/^[A-Z][A-Z0-9_]{0,29}$/.test(job.object_name) || !/^[A-Za-z0-9_-]{43}$/.test(job.lease) ||
      !Number.isFinite(Date.parse(job.expires_at)) || Date.parse(job.expires_at) <= now || Date.parse(job.expires_at) > now + 5 * 60_000) {
    throw new Error('invalid_job');
  }
  return [SAP_CLI, job.operation, job.object_name, '--conn', 'DS4'];
}

export function validateResult(result, job) {
  const meta = result?._evidence;
  if (!result || typeof result !== 'object' || Array.isArray(result) ||
      !meta || meta.trust !== 'UNTRUSTED_EVIDENCE' || meta.instructionAuthority !== false ||
      meta.system !== 'DS4' || meta.client !== '110' || meta.command !== job.operation || meta.object !== job.object_name ||
      !/^[0-9a-f]{64}$/.test(meta.contentSha256 || '') || (job.operation === 'tcode' && result.tcode !== job.object_name)) {
    throw new Error('invalid_evidence');
  }
  if (Buffer.byteLength(JSON.stringify(result)) > MAX_BYTES) throw new Error('output_limit');
  const canonical = value => {
    if (Array.isArray(value)) return value.map(canonical);
    if (value && typeof value === 'object') return Object.fromEntries(Object.keys(value).sort().map(k => [k, canonical(value[k])]));
    return value;
  };
  const content = Object.fromEntries(Object.entries(result).filter(([key]) => key !== '_evidence'));
  if (createHash('sha256').update(JSON.stringify(canonical(content))).digest('hex') !== meta.contentSha256) {
    throw new Error('invalid_evidence');
  }
  // Login identity is checked inside sap-eyes; the broker/model do not need it.
  return { ...result, _evidence: Object.fromEntries(Object.entries(meta).filter(([key]) => key !== 'user')) };
}

export function childEnvironment(source = process.env) {
  const allowed = ['PATH', 'HOME', 'TMPDIR', 'LANG', 'LC_ALL', 'SAPNWRFC_HOME', 'DYLD_LIBRARY_PATH', 'DYLD_FALLBACK_LIBRARY_PATH'];
  return Object.fromEntries(allowed.filter(k => source[k]).map(k => [k, source[k]]));
}

export async function executeJob(job, signal) {
  const args = jobArguments(job);
  return new Promise((resolve, reject) => {
    // No shell. No remotely selected binary, script path, system or credential source.
    const child = spawn(process.execPath, args, { env: childEnvironment(), stdio: ['ignore', 'pipe', 'ignore'], detached: true });
    let output = Buffer.alloc(0), failure = '';
    const kill = code => {
      failure = code;
      try { process.kill(-child.pid, 'SIGKILL'); } catch { /* already exited */ }
    };
    const cancel = () => kill('revoked');
    signal?.addEventListener('abort', cancel, { once: true });
    if (signal?.aborted) cancel();
    const timer = setTimeout(() => kill('timeout'), 70_000);
    child.stdout.on('data', chunk => {
      if (output.length + chunk.length > MAX_BYTES) kill('output_limit');
      else output = Buffer.concat([output, chunk]);
    });
    child.on('error', () => { clearTimeout(timer); signal?.removeEventListener('abort', cancel); reject(new Error('sap_failed')); });
    child.on('close', code => {
      clearTimeout(timer);
      signal?.removeEventListener('abort', cancel);
      if (failure || code !== 0) return reject(new Error(failure || 'sap_failed'));
      try { resolve(validateResult(JSON.parse(output.toString('utf8')), job)); }
      catch (error) { reject(new Error(error.message === 'output_limit' ? 'output_limit' : 'invalid_evidence')); }
    });
  });
}

export async function requestJson(origin, route, body, token = '') {
  const response = await fetch(origin + '/api/assistant/connector/' + route, {
    method: 'POST', redirect: 'error', signal: AbortSignal.timeout(10_000),
    headers: { 'Content-Type': 'application/json', ...(token ? { Authorization: 'Bearer ' + token } : {}) },
    body: JSON.stringify(body),
  });
  // Never print remote response/error text, URLs, headers, credentials or SAP stderr.
  if (!response.ok) throw new Error([401, 403].includes(response.status) ? 'authorization_failed' : 'server_error');
  const chunks = [];
  let size = 0;
  for await (const chunk of response.body) {
    size += chunk.length;
    if (size > 32_000) throw new Error('server_error');
    chunks.push(chunk);
  }
  return JSON.parse(Buffer.concat(chunks).toString('utf8'));
}

async function main() {
  if (process.platform !== 'darwin') throw new Error('此原型用于当前 Mac；Windows 适配尚未包含。');
  if (!process.stdin.isTTY) throw new Error('请在交互终端启动连接器。');
  if (!existsSync(SAP_CLI)) throw new Error('未找到本机 sap-eyes。此原型复用当前 Mac 的工具箱环境。');
  if (process.argv.length > 2) throw new Error('此原型不接受命令行参数；请在提示中输入服务器和配对码。');
  const terminal = createInterface({ input: process.stdin, output: process.stdout });
  const controller = new AbortController();
  let token = '', stopping = false, pulse;
  const stop = () => { stopping = true; controller.abort(); terminal.close(); };
  process.on('SIGINT', stop);
  process.on('SIGTERM', stop);
  console.log('\nSAP Desk 本机连接助手 · Mac 免安装试用版');
  console.log('仅 DS4/110；仅事务码映射、DDIC 元数据；不会修改 SAP 或读取业务表记录。');
  console.log('查询结果将发到你指定的 SAP Desk。密钥仅保存在本次进程内存，退出后需重新配对。\n');
  try {
    const origin = serverOrigin((await terminal.question('SAP Desk 地址 [http://127.0.0.1:5175]：')).trim() || 'http://127.0.0.1:5175');
    console.log('目标服务器：' + origin);
    const code = (await terminal.question('粘贴网页「本机 DS4」生成的一次性配对码：')).trim();
    if (!/^[A-Za-z0-9_-]{32}$/.test(code)) throw new Error('配对码格式不正确。');
    const paired = await requestJson(origin, 'pair', { code, name: '我的 Mac · 免安装连接器' });
    if (paired.protocol !== 'sapdesk-ds4-v1' || !/^[A-Za-z0-9_-]{43}$/.test(paired.token || '') || !UUID.test(paired.device?.id)) {
      throw new Error('服务器协议不匹配。');
    }
    token = paired.token;
    console.log('配对成功。请回网页提交查询；每次读取前在这里确认。Ctrl+C 退出。');
    let pulsing = false;
    pulse = setInterval(async () => {
      if (pulsing || stopping) return;
      pulsing = true;
      try { await requestJson(origin, 'agent/heartbeat', {}, token); }
      catch { console.log('\n设备连接中断，已停止接收任务。请重新启动并配对。'); stop(); }
      finally { pulsing = false; }
    }, 10_000);
    while (!stopping) {
      const { job } = await requestJson(origin, 'agent/claim', {}, token);
      if (!job) { await delay(2000, undefined, { signal: controller.signal }); continue; }
      jobArguments(job);
      const label = job.operation === 'tcode' ? '事务码映射' : 'DDIC 结构';
      let body;
      try {
        const timeout = AbortSignal.timeout(Math.max(1, Math.min(90_000, Date.parse(job.expires_at) - Date.now() - 75_000)));
        const answer = await terminal.question(`\n只读查询 DS4/110 · ${label} · ${job.object_name}\n读取后将发送到 ${origin}。输入 y 同意，其他输入拒绝：`,
          { signal: AbortSignal.any([controller.signal, timeout]) });
        if (stopping) break;
        if (answer.trim().toLowerCase() !== 'y') body = { error_code: 'declined' };
        else {
          // Recheck lease freshness after human confirmation.
          jobArguments(job);
          console.log('正在读取 DS4…');
          const result = await executeJob(job, controller.signal);
          body = { result };
        }
      } catch (error) {
        if (stopping) break;
        const known = ['sap_failed', 'timeout', 'invalid_evidence', 'output_limit', 'revoked'];
        body = { error_code: known.includes(error.message) ? error.message : 'timeout' };
      }
      await requestJson(origin, `agent/queries/${job.id}/complete`, { lease: job.lease, ...body }, token);
      console.log(body.result ? '只读结果已回传。请在网页查看，可选择附到 AI 对话。' : `本次未取得结果（${body.error_code}）。可回网页重新提交。`);
    }
  } finally {
    stopping = true;
    clearInterval(pulse);
    controller.abort();
    token = '';
    terminal.close();
    console.log('\n连接器已退出。需要立即撤销授权时，请在网页点击「断开」。');
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    const known = { authorization_failed: '配对码/设备授权无效或已过期，请重新配对。', server_error: '服务器暂未就绪，请检查地址和本地服务。' };
    console.error(known[error.message] || '连接器未能继续。请确认本机工具箱、Node.js 与服务器地址，重新启动后再试。');
    process.exitCode = 1;
  });
}
