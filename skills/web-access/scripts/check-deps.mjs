#!/usr/bin/env node
// Only run on a selected local-browser path. Imports and static reads never connect.
import { spawn, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { selectBrowser, knownBrowsers, findFallbackPort } from './browser-discovery.mjs';
import { bearerHeader, readRuntimeState } from './cdp-runtime.mjs';
import { migrateLegacyData, readConfigPreference, userDataPaths } from './user-data.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.CDP_PROXY_PORT || 3456);

export function quoteWindowsArg(value) {
  const str = String(value);
  if (!str) return '""';
  if (!/[\s"&|<>^()]/.test(str)) return str;
  // Standard Windows CreateProcess quoting: double backslashes before a quote or end-of-string
  return '"' + str.replace(/(\\*)"/g, '$1$1\\"').replace(/(\\+)$/, '$1$1') + '"';
}

export function buildWindowsProxyCommandLine(execPath, scriptPath, { browser = null, port = 3456, logFile = null } = {}) {
  const args = [execPath, scriptPath];
  if (browser) {
    if (!/^[a-z0-9-]+$/i.test(browser)) throw new Error(`非法浏览器标识：${browser}`);
    args.push('--browser', browser);
  }
  if (Number.isInteger(port) && port > 0 && port < 65536 && port !== 3456) {
    args.push('--port', String(port));
  }
  if (logFile) {
    args.push('--log-file', logFile);
  }
  return args.map(quoteWindowsArg).join(' ');
}

function tryStartProxyViaWindowsWmi(browser, logPath) {
  if (process.platform !== 'win32' || process.env.WEB_ACCESS_DISABLE_WMI_DETACH === '1') {
    return false;
  }
  // Win32_Process.Create spawns under WmiPrvSE.exe so the proxy survives parent Windows Job Object teardown
  // (e.g., Codex / Antigravity command runners that terminate child trees when a command exits).
  const cmdLine = buildWindowsProxyCommandLine(
    process.execPath,
    path.join(ROOT, 'scripts', 'cdp-proxy.mjs'),
    { browser, port: PORT, logFile: logPath },
  );
  const psScript = [
    '$ErrorActionPreference = "Stop"',
    '$si = New-CimInstance -ClassName Win32_ProcessStartup -Property @{ ShowWindow = [uint16]0 } -ClientOnly',
    '$res = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{ CommandLine = $env:WEB_ACCESS_PROXY_CMD; ProcessStartupInformation = $si }',
    'if ($res.ReturnValue -ne 0) { exit ([int]$res.ReturnValue) }',
  ].join('; ');
  try {
    const res = spawnSync('powershell.exe', ['-NoProfile', '-NonInteractive', '-Command', psScript], {
      env: { ...process.env, WEB_ACCESS_PROXY_CMD: cmdLine },
      stdio: 'ignore',
      windowsHide: true,
      timeout: 5000,
    });
    return res.status === 0;
  } catch {
    return false;
  }
}

function startProxy(browser) {
  const log = path.join(os.tmpdir(), 'cdp-proxy.log');
  if (tryStartProxyViaWindowsWmi(browser, log)) {
    return 'win32-wmi';
  }
  const fd = fs.openSync(log, 'a', 0o600);
  if (process.platform !== 'win32') fs.chmodSync(log, 0o600);
  try {
    const args = [path.join(ROOT, 'scripts', 'cdp-proxy.mjs')];
    if (browser) args.push('--browser', browser);
    const child = spawn(process.execPath, args, {
      detached: true, stdio: ['ignore', fd, fd], windowsHide: true,
    });
    child.unref();
  } finally { fs.closeSync(fd); }
  return 'spawn-detached';
}

function parseArgs(argv) {
  const opts = { quick: false, browser: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--quick') opts.quick = true;
    else if (arg === '--browser') {
      if (!argv[i + 1]) throw new Error('参数 --browser 缺少浏览器名称');
      opts.browser = argv[++i];
    } else if (arg.startsWith('--browser=')) opts.browser = arg.slice(10);
    else throw new Error(`未知参数：${arg}`);
  }
  return opts;
}

async function request(endpoint, { method = 'GET', timeout = 1000 } = {}) {
  const state = readRuntimeState(PORT);
  if (!state) return null;
  try {
    const response = await fetch(`http://127.0.0.1:${PORT}${endpoint}`, {
      method, headers: { Authorization: bearerHeader(state.token) }, signal: AbortSignal.timeout(timeout),
    });
    const data = await response.json();
    return { ok: response.ok, status: response.status, data };
  } catch { return null; }
}

function parseConfiguredPort(rawPort) {
  if (!rawPort) return null;
  const port = parseInt(rawPort, 10);
  return port > 0 && port < 65536 ? port : null;
}

function compatible(health, opts) {
  if (health.maintenance) throw new Error('共享服务正在维护，请稍后重试；保留现有实例');
  const expected = opts.browser || readConfigPreference('WEB_ACCESS_BROWSER');
  const fixedPort = !opts.browser ? parseConfiguredPort(readConfigPreference('WEB_ACCESS_DEBUG_PORT')) : null;
  if (fixedPort && fixedPort !== health.chromePort && health.connected) {
    throw new Error('当前连接与配置的固定端口不同；任务结束后使用 proxy-maintenance.mjs 切换');
  }
  if (!fixedPort && expected && health.browser?.id && health.browser.id !== expected) {
    throw new Error(`当前服务使用 ${health.browser.label || health.browser.id}，与本次浏览器选择不同；任务结束后使用 proxy-maintenance.mjs 切换`);
  }
}

async function resolveBrowser(opts) {
  const result = await selectBrowser(opts.browser);
  if (result.kind === 'ok') return result.browser.id === 'unknown' ? null : result.browser.id;
  if (result.kind === 'ambiguous') {
    console.log('browser: needs decision — 请选择本次浏览器，或在 ~/.web-access/config.env 设置 WEB_ACCESS_BROWSER');
    console.log(`  可用：${result.detected.map(b => b.label + ' (' + b.id + ')').join('、')}`);
    process.exitCode = 2;
    return undefined;
  }
  if (result.kind === 'empty' && await findFallbackPort()) return null;
  if (result.kind === 'mismatch') {
    const id = result.override || result.configured;
    const label = knownBrowsers().find(b => b.id === id)?.label || id;
    throw new Error(`所选 ${label} 不可连接。请打开该浏览器并检查 ${id === 'edge' ? 'edge' : 'chrome'}://inspect/#remote-debugging；不自动切换浏览器`);
  }
  if (result.kind === 'port_mismatch') throw new Error('WEB_ACCESS_DEBUG_PORT 配置的端口不可连接；请核对目标浏览器与端口');
  throw new Error('没有可用的本地浏览器远程调试连接');
}

async function main() {
  const opts = parseArgs(process.argv.slice(2));
  if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('需要 Node.js 22+');
  if (!Number.isInteger(PORT) || PORT < 1 || PORT > 65535) throw new Error('无效的 Proxy 端口');
  // Migration copies only; a failed migration must not create a default config over old preferences.
  const migrated = migrateLegacyData();
  if (migrated?.sources.length) console.log(`user-data: 已复制配置和站点经验到 ${migrated.dir}，旧文件保留`);
  const data = userDataPaths();
  if (!fs.existsSync(data.config)) {
    fs.mkdirSync(data.dir, { recursive: true, mode: 0o700 });
    fs.copyFileSync(path.join(ROOT, 'templates', 'config.env.template'), data.config, fs.constants.COPYFILE_EXCL);
    if (process.platform !== 'win32') fs.chmodSync(data.config, 0o600);
  }
  let health = await request('/health');
  let launchMode = null;
  const hadStaleState = !health?.ok && Boolean(readRuntimeState(PORT));
  if (health?.ok && health.data.status === 'ok') {
    compatible(health.data, opts);
  } else {
    // Detect an unauthenticated legacy service without borrowing its session or replacing it.
    try {
      const response = await fetch(`http://127.0.0.1:${PORT}/health`, { signal: AbortSignal.timeout(500) });
      const legacy = await response.json();
      if (response.ok && legacy.status === 'ok') throw new Error('当前端口是未认证的旧服务；请先查看维护迁移说明，不自动替换');
    } catch (error) { if (error.message.includes('未认证的旧服务')) throw error; }
    const browser = await resolveBrowser(opts);
    if (browser === undefined) return;
    launchMode = startProxy(opts.browser || browser);
    const deadline = Date.now() + (launchMode === 'win32-wmi' ? 5000 : 2500);
    do {
      await new Promise(resolve => setTimeout(resolve, 100));
      health = await request('/health');
    } while (!health?.ok && Date.now() < deadline);
    if (!health?.ok) throw new Error('Proxy 未及时启动；保留运行记录，不重复启动或终止其它服务');
    compatible(health.data, opts);
  }
  if (!health.data.connected) {
    // One connection request only; allow 26s+ for user to click Chrome remote-debugging prompt.
    const connected = await request('/connect', { method: 'POST', timeout: opts.quick ? 26000 : 30000 });
    if (!connected?.ok) throw new Error(connected?.data?.error || 'CDP 未及时就绪；本次不重复申请授权');
  }
  const probe = await request('/probe', { method: 'POST', timeout: opts.quick ? 3000 : 8000 });
  if (!probe?.ok) throw new Error(probe?.data?.error || '实时 CDP 检查失败；Profile 识别仍延迟至创建任务页');
  health = await request('/health');
  if (!health?.ok || !health.data.connected) throw new Error('Proxy 运行但浏览器连接不可用');
  compatible(health.data, opts);
  console.log(`proxy: ready (${health.data.browser?.label || '本地浏览器'}, connection checked, Profile deferred)`);
  if (process.platform === 'win32' && hadStaleState && launchMode === 'spawn-detached') {
    console.log('proxy: 检测到上次后台进程在 Windows 环境中被外部终止且 WMI 脱离未启用；若仍反复弹出 CDP 授权，请在普通 PowerShell 运行 platforms/win/start-cdp-proxy.ps1 常驻一次');
  }
  if (!health.data.instanceId) console.log('proxy: 仍在运行旧代码；空闲时使用维护命令完成一次性升级');
}

function isCliEntry() {
  if (!process.argv[1]) return false;
  try {
    return fs.realpathSync(path.resolve(process.argv[1])) === fs.realpathSync(fileURLToPath(import.meta.url));
  } catch {
    return path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
  }
}

if (isCliEntry()) {
  main().catch(error => { console.error(error.message); process.exitCode = 1; });
}
