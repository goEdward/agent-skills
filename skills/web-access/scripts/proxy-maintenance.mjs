#!/usr/bin/env node
// Explicit, authenticated maintenance only; never kill unrelated processes or reconnect for status.
import { spawn } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { bearerHeader, readRuntimeState } from './cdp-runtime.mjs';

const PORT = Number(process.env.CDP_PROXY_PORT || 3456);
const action = process.argv[2] || 'status';
const legacy = process.argv.includes('--legacy-stop');
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

async function health() {
  const state = readRuntimeState(PORT);
  if (!state) return null;
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}/health`, {
      headers: { Authorization: bearerHeader(state.token), Connection: 'close' }, signal: AbortSignal.timeout(1500),
    });
    const data = await response.json();
    if (!response.ok || data.status !== 'ok') throw new Error('端口不是可认证的 Proxy');
    return { state, data };
  } catch (error) {
    if (error.message === '端口不是可认证的 Proxy') throw error;
    // A stale state file does not prove that the listener belongs to us.
    if (process.kill) {
      try { process.kill(state.pid, 0); }
      catch (probe) { if (probe.code === 'ESRCH') return null; }
    }
    throw new Error('服务暂时无响应；保留现有进程，稍后重试');
  }
}

function publicStatus(info) {
  if (!info) return { status: 'stopped' };
  const h = info.data;
  return {
    status: h.maintenance ? 'maintaining' : h.connected ? 'ready' : 'running',
    browser: h.browser, pid: h.pid || info.state.pid, instanceId: h.instanceId,
    runningRevision: h.runningRevision, connected: h.connected,
    managedTabs: h.managedTabs, activeRequests: h.activeRequests,
    pendingCommands: h.pendingCommands, connectionState: h.connectionState,
    legacy: !h.instanceId,
  };
}

async function stop(info) {
  if (!info) return;
  const h = info.data;
  if (!h.instanceId && !legacy) throw new Error('这是旧的认证服务；空闲且 managedTabs=0 时可显式加 --legacy-stop 做一次性迁移；不自动强停');
  // Internal bridge pages do not occupy a task; the server makes the final busy decision.
  if (h.activeRequests > 0 || h.pendingCommands > 0 || ['discovering', 'connecting'].includes(h.connectionState) || (!h.instanceId && h.managedTabs > 0)) {
    throw new Error('共享服务仍有任务或连接进行中，拒绝停止');
  }
  const response = await fetch(`http://127.0.0.1:${PORT}/shutdown`, {
    method: 'POST', headers: { Authorization: bearerHeader(info.state.token), 'Content-Type': 'application/json', Connection: 'close' },
    body: JSON.stringify({ instanceId: h.instanceId }), signal: AbortSignal.timeout(4000),
  });
  const result = await response.json();
  if (!response.ok) throw new Error(result.error === 'busy' ? '共享服务仍有任务，拒绝停止' : result.error || '维护请求未被接受');
  if (h.instanceId && result.instanceId !== h.instanceId) throw new Error('停止确认的实例不一致，保留新实例');
  const deadline = Date.now() + 6000;
  while (Date.now() < deadline) {
    let current;
    try { current = await health(); }
    catch { await pause(100); continue; } // shutdown can reset a just-opened connection
    if (!current) {
      try { process.kill(info.state.pid, 0); }
      catch (probe) { if (probe.code === 'ESRCH') return; }
      await pause(100);
      continue;
    }
    if (current.state.pid !== info.state.pid || (h.instanceId && current.data.instanceId !== h.instanceId)) {
      throw new Error('其它维护者已替换服务；保留新实例，不再次启动');
    }
    await pause(100);
  }
  throw new Error('停止请求已接受，但服务未在窗口内退出；不强停、不重复启动');
}

async function start(browser) {
  const args = [path.join(ROOT, 'scripts', 'check-deps.mjs'), '--quick'];
  if (browser?.id && browser.id !== 'unknown') args.push('--browser', browser.id);
  await new Promise((resolve, reject) => {
    const child = spawn(process.execPath, args, { stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true });
    child.stdout.on('data', data => process.stderr.write(data));
    child.stderr.on('data', data => process.stderr.write(data));
    child.once('error', reject);
    child.once('exit', code => { if (code === 0) resolve(); else reject(new Error('服务启动检查未通过，保留现有运行状态')); });
  });
}

async function main() {
  if (!['status', 'stop', 'restart'].includes(action)) throw new Error('用法：proxy-maintenance.mjs status|stop|restart [--legacy-stop]');
  if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) throw new Error('无效的 Proxy 端口');
  const info = await health();
  if (action === 'status') { console.log(JSON.stringify(publicStatus(info))); return; }
  await stop(info);
  if (action === 'stop') { console.log(JSON.stringify({ status: 'stopped' })); return; }
  await start(info?.data.browser);
  const current = await health();
  if (!current?.data.connected) { process.exitCode = 3; console.log(JSON.stringify({ status: 'waitingAuthorization' })); return; }
  if (info?.data.browser?.id && current.data.browser?.id !== info.data.browser.id) throw new Error('重启后的浏览器身份不同，保留现有服务并报告冲突');
  if (info?.data.instanceId && current.data.instanceId === info.data.instanceId) throw new Error('仍是旧实例，未完成重启');
  console.log(JSON.stringify(publicStatus(current)));
}
main().catch(error => { console.error(error.message); process.exitCode = 2; });
