#!/usr/bin/env node
// CDP Proxy - 通过 HTTP API 操控用户日常浏览器（Chrome / Edge / Chromium 等）
// 要求：浏览器已开启 remote debugging（chrome://inspect#remote-debugging toggle）
// Node.js 22+（使用原生 WebSocket）

import http from 'node:http';
import { URL } from 'node:url';
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { createHash, randomUUID } from 'node:crypto';
import { createCdpConnection } from './cdp-connection.mjs';
import { createPageFrames, FrameError } from './page-frames.mjs';
import { readConfigPreference } from './user-data.mjs';
import { selectBrowser, findFallbackPort } from './browser-discovery.mjs';
import {
  bearerHeader,
  createRuntimeToken,
  isOwnedTarget,
  normalizeOwner,
  readRuntimeState,
  removeRuntimeState,
  tokenMatches,
  writeRuntimeState,
} from './cdp-runtime.mjs';
import { createProfileRouter } from './profile-selection.mjs';
import { redactUrl } from './url-safety.mjs';

// --- 解析命令行参数（--browser / --port / --log-file）---
function parseCliArgs() {
  const argv = process.argv.slice(2);
  const parsed = { browser: null, port: null, logFile: null };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === '--browser' && argv[i + 1]) parsed.browser = argv[++i];
    else if (arg.startsWith('--browser=')) parsed.browser = arg.slice('--browser='.length);
    else if (arg === '--port' && argv[i + 1]) parsed.port = parseInt(argv[++i], 10);
    else if (arg.startsWith('--port=')) parsed.port = parseInt(arg.slice('--port='.length), 10);
    else if (arg === '--log-file' && argv[i + 1]) parsed.logFile = argv[++i];
    else if (arg.startsWith('--log-file=')) parsed.logFile = arg.slice('--log-file='.length);
  }
  return parsed;
}
const CLI_ARGS = parseCliArgs();
const BROWSER_OVERRIDE = CLI_ARGS.browser;

// 当通过 Windows WMI 等脱离父进程方式启动时，无法继承父进程 fd，
// 通过 --log-file 将日志直接追加写入本地日志文件。
if (CLI_ARGS.logFile) {
  try {
    const logStream = fs.createWriteStream(CLI_ARGS.logFile, { flags: 'a', mode: 0o600 });
    const formatArgs = (items) => items.map((x) => (typeof x === 'string' ? x : String(x))).join(' ') + '\n';
    for (const method of ['log', 'warn', 'error']) {
      const orig = console[method].bind(console);
      console[method] = (...items) => {
        try { logStream.write(formatArgs(items)); } catch { /* ignore log write errors */ }
        orig(...items);
      };
    }
  } catch { /* fallback to default stdio */ }
}

function foregroundFallbackAllowed() {
  return /^(1|true|yes|on)$/i.test(readConfigPreference('WEB_ACCESS_ALLOW_FOREGROUND_FALLBACK') || '');
}
const PORT = (Number.isInteger(CLI_ARGS.port) && CLI_ARGS.port > 0 && CLI_ARGS.port < 65536)
  ? CLI_ARGS.port
  : parseInt(process.env.CDP_PROXY_PORT || '3456');
const MANAGED_TABS_FILE = path.join(os.tmpdir(), `cdp-proxy-managed-tabs-${PORT}.json`);
const RUNTIME_TOKEN = createRuntimeToken();
const MAX_BODY_BYTES = 1024 * 1024;
const INTERNAL_PROBE_OWNER = '__web_access_internal_probe__';
const sessions = new Map(); // targetId -> sessionId
const managedTabs = new Map(); // targetId -> { lastAccessed: number, owner: string | null }
const lifecycleEvents = new Map(); // sessionId -> recent Page.lifecycleEvent[]
const TAB_IDLE_TIMEOUT = parseInt(process.env.CDP_TAB_IDLE_TIMEOUT || '300000'); // 5 min default
const CLEANUP_INTERVAL = 60000; // sweep every 60s
const INSTANCE_ID = randomUUID();
const STARTED_AT = new Date().toISOString();
const revisionHash = createHash('sha256');
for (const file of ['cdp-proxy.mjs', 'cdp-connection.mjs', 'page-frames.mjs', 'profile-selection.mjs', 'cdp-runtime.mjs', 'url-safety.mjs', 'browser-discovery.mjs', 'user-data.mjs']) {
  revisionHash.update(fs.readFileSync(new URL(file, import.meta.url)));
}
const RUNNING_REVISION = revisionHash.digest('hex').slice(0, 12);
let activeRequests = 0;
let maintenance = false;

// 页面导航结果和 CDP 错误会回到 Agent 的输出；只在输出边界脱敏，
// 不修改实际导航所使用的 URL。
function redactOutputMessage(message) {
  return String(message ?? '').replace(/\bhttps?:\/\/[^\s"'<>]+/gi, (candidate) => redactUrl(candidate));
}

function redactOutputUrl(value) {
  return typeof value === 'string' ? redactUrl(value) : value ?? null;
}

function redactNavigationResult(result) {
  if (!result || typeof result !== 'object') return result;
  const output = { ...result };
  if ('url' in output) output.url = redactOutputUrl(output.url);
  if (typeof output.error === 'string') output.error = redactOutputMessage(output.error);
  if (typeof output.errorText === 'string') output.errorText = redactOutputMessage(output.errorText);
  return output;
}

function persistManagedTabs() {
  const tempFile = `${MANAGED_TABS_FILE}.${process.pid}.tmp`;
  try {
    fs.writeFileSync(tempFile, JSON.stringify([...managedTabs.entries()]), { mode: 0o600 });
    fs.renameSync(tempFile, MANAGED_TABS_FILE);
    try { fs.chmodSync(MANAGED_TABS_FILE, 0o600); } catch { /* Windows may ignore POSIX mode */ }
  } catch {
    try { fs.unlinkSync(tempFile); } catch { /* ignore persistence cleanup */ }
  }
}

function registerManagedTarget(targetId, owner, kind = 'page') {
  managedTabs.set(targetId, { lastAccessed: Date.now(), owner, kind });
  persistManagedTabs();
}

function isMissingTargetError(error) {
  const message = String(error?.message || error || '');
  return /no target with given id|target.*not found|could not find target/i.test(message);
}

async function closeRegisteredTarget(targetId) {
  if (!managedTabs.has(targetId)) return { closed: false, removed: false };
  try {
    const resp = await sendCDP('Target.closeTarget', { targetId });
    if (resp.error) {
      if (!isMissingTargetError(resp.error)) {
        return { closed: false, removed: false, error: resp.error.message || '关闭 tab 失败' };
      }
    } else if (resp.result?.success === false) {
      return { closed: false, removed: false, error: '浏览器拒绝关闭 tab' };
    }
    forgetSession(targetId);
    managedTabs.delete(targetId);
    persistManagedTabs();
    return { closed: !resp.error, removed: true };
  } catch (error) {
    if (!isMissingTargetError(error)) {
      return { closed: false, removed: false, error: error.message };
    }
    forgetSession(targetId);
    managedTabs.delete(targetId);
    persistManagedTabs();
    return { closed: false, removed: true };
  }
}

try {
  const entries = JSON.parse(fs.readFileSync(MANAGED_TABS_FILE, 'utf8'));
  try { fs.chmodSync(MANAGED_TABS_FILE, 0o600); } catch { /* Windows may ignore POSIX mode */ }
  let normalizedBridge = false;
  for (const [targetId, info] of entries) {
    if (targetId && info && Number.isFinite(info.lastAccessed)) {
      // 重启后的新进程不再持有旧桥接页引用，降级为 profile-probe 以便闲置回收，避免孤儿页长期滞留。
      if (info.kind === 'profile-bridge') {
        managedTabs.set(targetId, { ...info, kind: 'profile-probe' });
        normalizedBridge = true;
      } else {
        managedTabs.set(targetId, info);
      }
    }
  }
  if (normalizedBridge) persistManagedTabs();
} catch { /* first run or stale registry */ }

// --- WebSocket 兼容层 ---
let WS;
if (typeof globalThis.WebSocket !== 'undefined') {
  // Node 22+ 原生 WebSocket（浏览器兼容 API）
  WS = globalThis.WebSocket;
} else {
  // 回退到 ws 模块
  try {
    WS = (await import('ws')).default;
  } catch {
    console.error('[CDP Proxy] 错误：Node.js 版本 < 22 且未安装 ws 模块');
    console.error('  解决方案：升级到 Node.js 22+ 或执行 npm install -g ws');
    process.exit(1);
  }
}

// proxy 启动时连接到的浏览器（用于 /health 暴露给 check-deps 比较）
let connectedBrowser = null; // { id, label, source }

// pin 首次成功连接的浏览器 id。重连时只接受同一 id，避免悄悄降级到别的浏览器。
let pinnedBrowserId = null;

// --- 自动发现浏览器调试端口 ---
// 决策完全委派给 browser-discovery.selectBrowser；此处只做日志和返回结构包装。
async function discoverChromePort() {
  const result = await selectBrowser(BROWSER_OVERRIDE);
  if (result.kind === 'ok') {
    if (pinnedBrowserId && pinnedBrowserId !== result.browser.id) {
      throw new Error(
        `本次连接的浏览器已经是 ${pinnedBrowserId}，不会自动切到 ${result.browser.id}。` +
        `如果想换成 ${result.browser.id}，请先运行 cdp-call.mjs shutdown，再重新检查依赖。`
      );
    }
    pinnedBrowserId = result.browser.id;
    connectedBrowser = { id: result.browser.id, label: result.browser.label, source: result.source };
    const tag =
      result.source === 'override'
        ? '[--browser 指定]'
        : result.source === 'port-preference'
          ? '[config.env 调试端口]'
          : '[config.env 偏好]';
    console.log(`[CDP Proxy] 选用 ${result.browser.label} (端口 ${result.browser.port}${result.browser.wsPath ? '，带 wsPath' : ''}) ${tag}`);
    return { port: result.browser.port, wsPath: result.browser.wsPath };
  }
  if (result.kind === 'port_mismatch') {
    throw new Error(
      `config.env 固定调试端口是 "${result.configuredPort}"，但当前未连通。` +
      `请先让目标浏览器以该端口开启远程调试后重试；` +
      `若不再使用固定端口，请删除 WEB_ACCESS_DEBUG_PORT 改回浏览器偏好模式。`
    );
  }
  // mismatch：有显式偏好但未检测到 —— 硬错，绝不降级
  if (result.kind === 'mismatch') {
    const expected = result.override || result.configured;
    const src = result.source === 'override' ? '本次指定' : '默认偏好';
    const inspectScheme = expected === 'edge' ? 'edge' : 'chrome';
    throw new Error(
      `${src}的浏览器是 "${expected}"，但没连上。Agent 处理顺序：` +
      `(1) 先用系统命令打开 ${expected}（按平台选择，如 macOS 的 open -a），稍等后重试请求；` +
      `(2) 若仍失败，说明远程调试开关没启用 —— 告知用户在地址栏访问 ${inspectScheme}://inspect/#remote-debugging 勾选 "Allow remote debugging for this browser instance"。`
    );
  }
  // 已 pin 过浏览器（如首次连上 edge 后 edge 退出）：拒绝任何 fallback
  if (pinnedBrowserId) {
    const inspectScheme = pinnedBrowserId === 'edge' ? 'edge' : 'chrome';
    throw new Error(
      `本次连接的浏览器是 ${pinnedBrowserId}，但现在没连上。Agent 处理顺序：` +
      `(1) 先用系统命令打开 ${pinnedBrowserId}（按平台选择），稍等后重试请求；` +
      `(2) 若仍失败，告知用户在地址栏访问 ${inspectScheme}://inspect/#remote-debugging 重新勾选允许。` +
      `若想换成其他浏览器，请先运行 proxy-maintenance.mjs stop，再重新检查依赖。`
    );
  }
  // 仅在「从未成功连接 + 无偏好/override」时允许固定端口兜底（手动 --remote-debugging-port 启动场景）
  const fallbackPort = await findFallbackPort();
  if (fallbackPort !== null) {
    connectedBrowser = { id: 'unknown', label: '未知（通过手动调试端口连接）', source: 'fallback' };
    console.log(`[CDP Proxy] 通过手动调试端口连接: ${fallbackPort}`);
    return { port: fallbackPort, wsPath: null };
  }
  return null;
}

function getWebSocketUrl(port, wsPath) {
  if (wsPath) return `ws://127.0.0.1:${port}${wsPath}`;
  return `ws://127.0.0.1:${port}/devtools/browser`;
}

// --- WebSocket 连接管理 ---
let chromePort = null;
let chromeWsPath = null;

let reconnectBlockedUntil = 0;
const RECONNECT_COOLDOWN = Number(process.env.CDP_RECONNECT_COOLDOWN || 30000);
// Chrome remote-debugging 授权弹窗需要用户切窗口点击，默认给足 25 秒避免误超时重连
const CONNECT_TIMEOUT_MS = Number(process.env.CDP_CONNECT_TIMEOUT_MS || 25000);
const profiles = createProfileRouter({
  sendCDP, ensureSession, managedTabs, registerManagedTarget, closeManagedTargets,
  forgetSession, touchTab, persistManagedTabs, readConfigPreference,
  getBrowserId: () => connectedBrowser?.id, internalOwner: INTERNAL_PROBE_OWNER,
});
const pageFrames = createPageFrames({ send: sendCDP, ensureSession });
const connection = createCdpConnection({
  WebSocketImpl: WS,
  connectTimeoutMs: CONNECT_TIMEOUT_MS,
  discoverUrl: async () => {
    if (Date.now() < reconnectBlockedUntil) {
      throw new Error(`CDP 刚断开或授权被拒绝，${Math.ceil((reconnectBlockedUntil - Date.now()) / 1000)} 秒后再试`);
    }
    const discovered = await discoverChromePort();
    if (!discovered) throw new Error('所选浏览器远程调试不可用，请检查目标浏览器的 remote-debugging 开关');
    chromePort = discovered.port;
    chromeWsPath = discovered.wsPath;
    return getWebSocketUrl(chromePort, chromeWsPath);
  },
  onConnect: () => {
    reconnectBlockedUntil = 0;
    console.log(`[CDP Proxy] 已连接浏览器 (端口 ${chromePort})`);
  },
  onDisconnect: error => {
    chromePort = null;
    chromeWsPath = null;
    profiles.reset('CDP 已断开');
    sessions.clear();
    lifecycleEvents.clear();
    portGuardedSessions.clear();
    if (Date.now() >= reconnectBlockedUntil) reconnectBlockedUntil = Date.now() + RECONNECT_COOLDOWN;
    console.warn('[CDP Proxy]', redactOutputMessage(error.message));
  },
  onEvent: msg => {
    if (msg.method === 'Target.attachedToTarget') {
      const { sessionId, targetInfo } = msg.params;
      sessions.set(targetInfo.targetId, sessionId);
    }
    if (msg.method === 'Target.detachedFromTarget') {
      const sid = msg.params?.sessionId;
      lifecycleEvents.delete(sid);
      portGuardedSessions.delete(sid);
      for (const [targetId, sessionId] of sessions) {
        if (sessionId === sid) sessions.delete(targetId);
      }
    }
    if (msg.method === 'Page.lifecycleEvent' && msg.sessionId) {
      const events = lifecycleEvents.get(msg.sessionId) || [];
      events.push({ receivedAt: Date.now(), ...msg.params });
      lifecycleEvents.set(msg.sessionId, events.slice(-100));
    }
    if (msg.method === 'Fetch.requestPaused') {
      sendCDP('Fetch.failRequest', { requestId: msg.params.requestId, errorReason: 'ConnectionRefused' }, msg.sessionId).catch(() => {});
    }
  },
  onDiagnostic: error => console.warn('[CDP Proxy]', redactOutputMessage(error.message)),
});
function connect() { return connection.connect(); }
function sendCDP(method, params = {}, sessionId = null) { return connection.send(method, params, sessionId); }

// 已启用端口拦截的 session 集合（避免重复启用）
const portGuardedSessions = new Set();

function forgetSession(targetId) {
  const sessionId = sessions.get(targetId);
  if (sessionId) {
    lifecycleEvents.delete(sessionId);
    portGuardedSessions.delete(sessionId);
  }
  sessions.delete(targetId);
}

async function ensureSession(targetId) {
  if (sessions.has(targetId)) return sessions.get(targetId);
  const resp = await sendCDP('Target.attachToTarget', { targetId, flatten: true });
  if (resp.result?.sessionId) {
    const sid = resp.result.sessionId;
    sessions.set(targetId, sid);
    // 启用调试端口探测拦截
    await enablePortGuard(sid);
    return sid;
  }
  throw new Error('attach 失败: ' + JSON.stringify(resp.error));
}

// 拦截页面对 Chrome 调试端口的探测（反风控）
// 只拦截 127.0.0.1:{chromePort} 的请求，不影响其他任何本地服务
async function enablePortGuard(sessionId) {
  if (!chromePort || portGuardedSessions.has(sessionId)) return;
  try {
    await sendCDP('Fetch.enable', {
      patterns: [
        { urlPattern: `http://127.0.0.1:${chromePort}/*`, requestStage: 'Request' },
        { urlPattern: `http://localhost:${chromePort}/*`, requestStage: 'Request' },
      ]
    }, sessionId);
    portGuardedSessions.add(sessionId);
  } catch { /* Fetch 域启用失败不影响主流程 */ }
}

// --- 闲置 Tab 自动清理 ---
function touchTab(targetId) {
  const entry = managedTabs.get(targetId);
  if (entry) {
    entry.lastAccessed = Date.now();
    persistManagedTabs();
  }
}

async function preserveBrowserWindowBeforeClosing(targetIds) {
  const closingIds = new Set(targetIds);
  const current = await sendCDP('Target.getTargets');
  const pages = current.result.targetInfos.filter(t => t.type === 'page');
  const closingPages = pages.filter(t => closingIds.has(t.targetId));
  if (!closingPages.length || pages.some(t => !closingIds.has(t.targetId))) return null;

  const source = closingPages[0];
  const params = { url: 'about:blank', background: true };
  if (source.browserContextId) params.browserContextId = source.browserContextId;
  try {
    const created = await sendCDP('Target.createTarget', params);
    if (!created.error) {
      console.log(`[CDP Proxy] Preserved browser window with background blank tab: ${created.result.targetId}`);
      return created.result.targetId;
    }
  } catch { /* fall through to the last-resort window.open path */ }

  if (!foregroundFallbackAllowed()) {
    throw new Error('后台创建保留标签页失败；前台 window.open 回退已禁用');
  }

  const beforeIds = new Set(pages.map(t => t.targetId));
  try {
    const sid = await ensureSession(source.targetId);
    await sendCDP('Runtime.evaluate', {
      expression: 'window.open("about:blank", "_blank"); undefined',
      returnByValue: true,
    }, sid);
    for (let i = 0; i < 20; i++) {
      await new Promise(resolve => setTimeout(resolve, 100));
      const updated = await sendCDP('Target.getTargets');
      const opened = updated.result.targetInfos.find(t =>
        t.type === 'page' && !beforeIds.has(t.targetId)
      );
      if (opened) {
        console.log(`[CDP Proxy] Preserved browser window with blank tab: ${opened.targetId}`);
        return opened.targetId;
      }
    }
  } catch { /* handled below */ }
  throw new Error('创建保留标签页失败');
}

async function closeManagedTargets(targetIds) {
  const targets = targetIds.filter(targetId => managedTabs.has(targetId));
  if (!targets.length) return { closed: 0, removed: 0, retained: 0, replacementTargetId: null };
  const replacementTargetId = await preserveBrowserWindowBeforeClosing(targets);
  let closed = 0;
  let retained = 0;
  for (const targetId of targets) {
    const result = await closeRegisteredTarget(targetId);
    if (result.closed) closed++;
    if (!result.removed) {
      retained++;
      console.warn(`[CDP Proxy] Tab cleanup deferred: ${targetId}${result.error ? ` (${result.error})` : ''}`);
    }
  }
  return { closed, removed: targets.length - retained, retained, replacementTargetId };
}

async function cleanupIdleTabs() {
  if (!connection.connected) return;
  const now = Date.now();
  for (const [targetId, info] of managedTabs) {
    if (info.kind === 'profile-bridge') continue;
    if (now - info.lastAccessed < TAB_IDLE_TIMEOUT) continue;
    try { await preserveBrowserWindowBeforeClosing([targetId]); } catch (e) {
      console.warn(`[CDP Proxy] Could not preserve browser window: ${e.message}`);
      continue;
    }
    const result = await closeRegisteredTarget(targetId);
    if (result.removed) {
      console.log(`[CDP Proxy] Auto-cleaned idle tab: ${targetId}`);
    } else {
      console.warn(`[CDP Proxy] Idle tab cleanup deferred: ${targetId}${result.error ? ` (${result.error})` : ''}`);
    }
  }
}

async function closeAllManagedTabs() {
  const targets = [...managedTabs.keys()];
  if (!targets.length) return { closed: 0, retained: 0 };
  if (!connection.connected) {
    return { closed: 0, retained: targets.length };
  }
  try { await preserveBrowserWindowBeforeClosing(targets); } catch (e) {
    console.warn(`[CDP Proxy] Could not preserve browser window: ${e.message}`);
    return { closed: 0, retained: targets.length };
  }
  let closed = 0;
  let retained = 0;
  for (const targetId of targets) {
    const result = await closeRegisteredTarget(targetId);
    if (result.closed) closed++;
    if (!result.removed) retained++;
  }
  console.log(`[CDP Proxy] Shutdown cleanup: closed ${closed}, retained ${retained}`);
  return { closed, retained };
}

// --- 导航与页面加载等待 ---
const delay = (ms) => new Promise(resolve => setTimeout(resolve, ms));

async function getPageState(sessionId) {
  const resp = await sendCDP('Runtime.evaluate', {
    expression: 'JSON.stringify({url: location.href, ready: document.readyState, timeOrigin: performance.timeOrigin})',
    returnByValue: true,
  }, sessionId);
  if (resp.error) throw new Error(resp.error.message || '读取页面状态失败');
  const raw = resp.result?.result?.value;
  return raw ? JSON.parse(raw) : null;
}

async function prepareNavigation(sessionId) {
  const pageEnabled = await sendCDP('Page.enable', {}, sessionId);
  if (pageEnabled.error) throw new Error(pageEnabled.error.message || 'Page.enable 失败');
  const lifecycleEnabled = await sendCDP('Page.setLifecycleEventsEnabled', { enabled: true }, sessionId);
  if (lifecycleEnabled.error) throw new Error(lifecycleEnabled.error.message || '启用页面生命周期事件失败');
  return getPageState(sessionId);
}

function sawLoadEvent(sessionId, startedAt, loaderId) {
  const events = lifecycleEvents.get(sessionId) || [];
  return events.some(event =>
    event.receivedAt >= startedAt &&
    event.name === 'load' &&
    (!loaderId || event.loaderId === loaderId)
  );
}

async function waitForLoad(sessionId, { beforeState, loaderId = null, startedAt, timeoutMs = 15000 }) {
  const deadline = startedAt + timeoutMs;
  let lastState = beforeState;

  while (Date.now() < deadline) {
    try {
      const state = await getPageState(sessionId);
      if (state) lastState = state;
      const documentChanged = Boolean(state) && (
        !beforeState ||
        state.url !== beforeState.url ||
        state.timeOrigin !== beforeState.timeOrigin
      );
      const lifecycleLoaded = sawLoadEvent(sessionId, startedAt, loaderId);
      if (state?.ready === 'complete' && (documentChanged || lifecycleLoaded)) {
        return {
          loaded: true,
          waitStatus: lifecycleLoaded ? 'load' : 'complete',
          url: state.url,
          elapsedMs: Date.now() - startedAt,
        };
      }
    } catch { /* 页面切换期间 execution context 暂时不可用，继续等待 */ }
    await delay(100);
  }

  return {
    loaded: false,
    waitStatus: 'timeout',
    url: lastState?.url || null,
    elapsedMs: Date.now() - startedAt,
  };
}

async function navigateAndWait(sessionId, url, timeoutMs = 15000) {
  const beforeState = await prepareNavigation(sessionId);
  const startedAt = Date.now();
  const resp = await sendCDP('Page.navigate', { url }, sessionId);
  if (resp.error) throw new Error(resp.error.message || 'Page.navigate 失败');
  const navigation = resp.result || {};
  if (navigation.errorText) {
    return {
      ...navigation,
      loaded: false,
      waitStatus: 'navigation-error',
      url: beforeState?.url || null,
      elapsedMs: Date.now() - startedAt,
    };
  }
  const load = await waitForLoad(sessionId, {
    beforeState,
    loaderId: navigation.loaderId || null,
    startedAt,
    timeoutMs,
  });
  return { ...navigation, ...load };
}

// --- 读取 POST body ---
async function readBody(req) {
  const chunks = [];
  let bytes = 0;
  for await (const chunk of req) {
    const buffer = Buffer.from(chunk);
    bytes += buffer.length;
    if (bytes > MAX_BODY_BYTES) {
      const error = new Error(`请求体超过 ${MAX_BODY_BYTES} bytes`);
      error.statusCode = 413;
      throw error;
    }
    chunks.push(buffer);
  }
  return Buffer.concat(chunks).toString('utf8');
}

const ENDPOINT_METHODS = new Map([
  ['/health', 'GET'],
  ['/connect', 'POST'],
  ['/probe', 'POST'],
  ['/targets', 'GET'],
  ['/new', 'POST'],
  ['/close', 'POST'],
  ['/close-owned', 'POST'],
  ['/navigate', 'POST'],
  ['/back', 'POST'],
  ['/eval', 'POST'],
  ['/click', 'POST'],
  ['/clickAt', 'POST'],
  ['/setFiles', 'POST'],
  ['/scroll', 'POST'],
  ['/screenshot', 'GET'],
  ['/info', 'GET'],
  ['/frames', 'GET'],
  ['/clickXY', 'POST'],
  ['/shutdown', 'POST'],
]);
const OWNER_ONLY_ENDPOINTS = new Set(['/targets', '/new', '/close-owned']);
const TARGET_ENDPOINTS = new Set([
  '/close', '/navigate', '/back', '/eval', '/click', '/clickAt',
  '/setFiles', '/scroll', '/screenshot', '/info', '/frames', '/clickXY',
]);

function sendJson(res, statusCode, value) {
  res.statusCode = statusCode;
  res.setHeader('Content-Type', 'application/json; charset=utf-8');
  res.end(JSON.stringify(value));
}

function requestOwner(q) {
  return normalizeOwner(q.owner);
}

function requireOwner(q, res) {
  const owner = requestOwner(q);
  if (!owner) {
    sendJson(res, 400, { error: '缺少 owner 参数' });
    return null;
  }
  if (owner === INTERNAL_PROBE_OWNER) {
    sendJson(res, 403, { error: '保留 owner 仅供 Proxy 内部使用' });
    return null;
  }
  return owner;
}

function requireOwnedTarget(q, res) {
  const owner = requireOwner(q, res);
  if (!owner) return null;
  if (!q.target) {
    sendJson(res, 400, { error: '缺少 target 参数' });
    return null;
  }
  if (!isOwnedTarget(managedTabs, q.target, owner)) {
    sendJson(res, 403, { error: '拒绝访问非本任务创建的 tab' });
    return null;
  }
  const entry = managedTabs.get(q.target);
  touchTab(q.target);
  return { owner, entry };
}

function authorizeRequest(req, res, pathname) {
  const expectedMethod = ENDPOINT_METHODS.get(pathname);
  if (!expectedMethod) {
    sendJson(res, 404, { error: '未知端点' });
    return false;
  }
  const host = String(req.headers.host || '').toLowerCase();
  if (host !== `127.0.0.1:${PORT}` && host !== `localhost:${PORT}`) {
    sendJson(res, 403, { error: '拒绝非本机 Host' });
    return false;
  }
  if (req.headers.origin || req.headers['sec-fetch-site']) {
    sendJson(res, 403, { error: '拒绝浏览器来源请求' });
    return false;
  }
  if (!tokenMatches(req.headers.authorization, RUNTIME_TOKEN)) {
    res.setHeader('WWW-Authenticate', 'Bearer');
    sendJson(res, 401, { error: '未认证' });
    return false;
  }
  if (req.method !== expectedMethod) {
    res.setHeader('Allow', expectedMethod);
    sendJson(res, 405, { error: `此端点只允许 ${expectedMethod}` });
    return false;
  }
  return true;
}

// --- HTTP API ---
const server = http.createServer(async (req, res) => {
  const parsed = new URL(req.url, `http://localhost:${PORT}`);
  const pathname = parsed.pathname;
  const q = Object.fromEntries(parsed.searchParams);
  if (!authorizeRequest(req, res, pathname)) return;

  let operationActive = false;
  try {
    // /health 不触发浏览器连接，但仍需要认证。
    if (pathname === '/health') {
      const connected = connection.connected;
      sendJson(res, 200, {
        status: 'ok',
        connected,
        browser: connectedBrowser,
        sessions: sessions.size,
        managedTabs: managedTabs.size,
        chromePort,
        pid: process.pid, instanceId: INSTANCE_ID, startedAt: STARTED_AT,
        runningRevision: RUNNING_REVISION, connectionState: connection.state,
        connectionAttempts: connection.attemptCount, pendingCommands: connection.pendingCount,
        activeRequests, maintenance,
      });
      return;
    }

    if (pathname === '/shutdown') {
      let body;
      try { body = JSON.parse(await readBody(req)); }
      catch { sendJson(res, 400, { error: '维护请求需要 instanceId JSON' }); return; }
      if (body?.instanceId !== INSTANCE_ID) {
        sendJson(res, 409, { error: '服务实例已变化', instanceId: INSTANCE_ID }); return;
      }
      const taskTabs = [...managedTabs.values()].filter(info => info.owner !== INTERNAL_PROBE_OWNER).length;
      if (activeRequests || taskTabs || connection.pendingCount || ['discovering', 'connecting'].includes(connection.state)) {
        sendJson(res, 409, { error: 'busy', activeRequests, managedTabs: taskTabs, pendingCommands: connection.pendingCount }); return;
      }
      maintenance = true;
      res.once('finish', () => { void shutdownProxy('API shutdown'); });
      sendJson(res, 202, { status: 'stopping', instanceId: INSTANCE_ID });
      return;
    }
    if (maintenance) { sendJson(res, 503, { error: '服务正在维护' }); return; }
    activeRequests++;
    operationActive = true;

    if (pathname === '/connect') {
      if (q.resetContext === '1' || q.resetContext === 'true') {
        profiles.reset('收到重新定位请求');
      }
      await connect();
      sendJson(res, 200, { status: 'ok', connected: true, browser: connectedBrowser });
      return;
    }

    if (pathname === '/probe') {
      await connect();
      // 健康检查只验证实时 CDP 连接，不创建 tab。Profile 的严格识别延迟到
      // 真正调用 /new 时执行，避免普通搜索或静态网页读取抢占浏览器焦点。
      const targets = await sendCDP('Target.getTargets');
      if (targets.error) throw new Error(targets.error.message || '无法读取浏览器目标');
      const pageCount = (targets.result?.targetInfos || []).filter(t => t.type === 'page').length;
      sendJson(res, 200, {
        status: 'ok', contextCreatable: true, profileCheck: 'deferred', pageCount,
      });
      return;
    }

    let owner = null;
    if (OWNER_ONLY_ENDPOINTS.has(pathname)) {
      owner = requireOwner(q, res);
      if (!owner) return;
    } else if (TARGET_ENDPOINTS.has(pathname)) {
      const owned = requireOwnedTarget(q, res);
      if (!owned) return;
      owner = owned.owner;
    }

    await connect();

    // GET /targets - 仅列出同一 owner 的受管页面
    if (pathname === '/targets') {
      const resp = await sendCDP('Target.getTargets');
      if (resp.error) throw new Error(resp.error.message || '读取目标列表失败');
      const allowed = new Set(
        [...managedTabs.entries()].filter(([, info]) => info.owner === owner).map(([targetId]) => targetId)
      );
      const pages = (resp.result?.targetInfos || [])
        .filter(t => t.type === 'page' && allowed.has(t.targetId))
        .map(t => ({ targetId: t.targetId, type: t.type, title: t.title, url: redactUrl(t.url) }));
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify(pages, null, 2));
    }

    // POST /new?url=xxx&owner=xxx - 创建新后台 tab
    else if (pathname === '/new') {
      const targetUrl = q.url || 'about:blank';
      let targetId;

      // background:true 不会像 window.open 那样抢占用户前台，也不会因识别超时遗留无主空白页。
      const resp = await profiles.createTarget({ url: 'about:blank', background: true });
      targetId = resp.result.targetId;
      registerManagedTarget(targetId, owner);

      let load = { loaded: true, waitStatus: 'blank', url: 'about:blank', elapsedMs: 0 };
      if (targetUrl !== 'about:blank') {
        try {
          const sid = await ensureSession(targetId);
          load = await navigateAndWait(sid, targetUrl);
        } catch (e) {
          load = { loaded: false, waitStatus: 'error', url: null, elapsedMs: 0, error: redactOutputMessage(e.message) };
        }
      }

      sendJson(res, 200, { targetId, ...redactNavigationResult(load) });
    }

    // POST /close?target=xxx&owner=xxx - 关闭 tab
    else if (pathname === '/close') {
      const result = await closeManagedTargets([q.target]);
      sendJson(res, 200, { success: result.retained === 0, ...result });
    }

    // POST /close-owned?owner=xxx - 任务结束时批量关闭该 Agent 自己的 tab
    else if (pathname === '/close-owned') {
      const targets = [...managedTabs.entries()]
        .filter(([, info]) => info.owner === owner)
        .map(([targetId]) => targetId);
      const result = await closeManagedTargets(targets);
      sendJson(res, 200, { owner, ...result });
    }

    // POST /navigate?target=xxx&owner=xxx&url=yyy - 导航（自动等待加载）
    else if (pathname === '/navigate') {
      if (!q.url) {
        sendJson(res, 400, { error: '缺少 url 参数' });
        return;
      }
      const sid = await ensureSession(q.target);
      const result = await navigateAndWait(sid, q.url);
      sendJson(res, 200, redactNavigationResult(result));
    }

    // POST /back?target=xxx&owner=xxx - 后退
    else if (pathname === '/back') {
      const sid = await ensureSession(q.target);
      const beforeState = await prepareNavigation(sid);
      const historyResp = await sendCDP('Page.getNavigationHistory', {}, sid);
      if (historyResp.error) throw new Error(historyResp.error.message || '读取导航历史失败');
      const { currentIndex, entries } = historyResp.result;
      if (currentIndex <= 0) {
        sendJson(res, 200, { ok: false, loaded: true, waitStatus: 'no-history', url: redactOutputUrl(beforeState?.url), elapsedMs: 0 });
        return;
      }
      const startedAt = Date.now();
      const navResp = await sendCDP('Page.navigateToHistoryEntry', { entryId: entries[currentIndex - 1].id }, sid);
      if (navResp.error) throw new Error(navResp.error.message || '后退失败');
      const load = await waitForLoad(sid, { beforeState, startedAt });
      sendJson(res, 200, { ok: true, ...redactNavigationResult(load) });
    }

    else if (pathname === '/frames') {
      const frames = await pageFrames.listFrames(q.target);
      sendJson(res, 200, frames.map(frame => ({
        ...frame, url: redactOutputUrl(frame.url),
        ...(frame.src ? { src: redactUrl(frame.src) } : {}),
      })));
    }
    else if (pathname === '/clickXY') {
      const x = Number(q.x), y = Number(q.y);
      if (!q.x?.trim() || !q.y?.trim() || !Number.isFinite(x) || !Number.isFinite(y) || x < 0 || y < 0) {
        sendJson(res, 400, { error: '需要有限的非负 x、y 视口坐标' }); return;
      }
      const route = await pageFrames.routePoint(q.target, x, y);
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
        const response = await sendCDP('Input.dispatchMouseEvent', {
          type, x: route.x, y: route.y, button: type === 'mouseMoved' ? 'none' : 'left',
          buttons: type === 'mousePressed' ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1,
        }, route.sessionId);
        if (response.error) throw new Error(response.error.message);
      }
      sendJson(res, 200, { clicked: true, x, y, ...(route.frameId ? { frameId: route.frameId, depth: route.depth } : {}) });
    }

    // POST /eval?target=xxx - 执行 JS
    else if (pathname === '/eval') {
      let sid, contextId;
      if (q.frame !== undefined) ({ sessionId: sid, contextId } = await pageFrames.frameContext(q.target, q.frame));
      else sid = await ensureSession(q.target);
      const body = await readBody(req);
      const expr = body || q.expr || 'document.title';
      const resp = await sendCDP('Runtime.evaluate', {
        expression: expr,
        returnByValue: true,
        awaitPromise: true,
        ...(contextId ? { contextId } : {}),
      }, sid);
      if (resp.error) throw new Error(resp.error.message);
      if (resp.result?.result?.value !== undefined) {
        sendJson(res, 200, { value: resp.result.result.value });
      } else if (resp.result?.exceptionDetails) {
        sendJson(res, 400, { error: resp.result.exceptionDetails.text });
      } else {
        sendJson(res, 200, resp.result);
      }
    }

    // POST /click?target=xxx - 点击（body 为 CSS 选择器）
    // POST /click?target=xxx — JS 层面点击（简单快速，覆盖大多数场景）
    else if (pathname === '/click') {
      const sid = await ensureSession(q.target);
      const selector = await readBody(req);
      if (!selector) {
        sendJson(res, 400, { error: 'POST body 需要 CSS 选择器' });
        return;
      }
      const selectorJson = JSON.stringify(selector);
      const js = `(() => {
        const el = document.querySelector(${selectorJson});
        if (!el) return { error: '未找到元素: ' + ${selectorJson} };
        el.scrollIntoView({ block: 'center' });
        el.click();
        return { clicked: true, tag: el.tagName, text: (el.textContent || '').slice(0, 100) };
      })()`;
      const resp = await sendCDP('Runtime.evaluate', {
        expression: js,
        returnByValue: true,
        awaitPromise: true,
      }, sid);
      if (resp.error) throw new Error(resp.error.message || '点击执行失败');
      if (resp.result?.result?.value) {
        const val = resp.result.result.value;
        sendJson(res, val.error ? 400 : 200, val);
      } else {
        sendJson(res, 200, resp.result);
      }
    }

    // POST /clickAt?target=xxx — CDP 浏览器级鼠标点击（比 el.click() 更接近指针输入，但不保证用户激活或绕过自动化检测）
    else if (pathname === '/clickAt') {
      const sid = await ensureSession(q.target);
      const selector = await readBody(req);
      if (!selector) {
        sendJson(res, 400, { error: 'POST body 需要 CSS 选择器' });
        return;
      }
      const selectorJson = JSON.stringify(selector);
      const js = `(() => {
        const el = document.querySelector(${selectorJson});
        if (!el) return { error: '未找到元素: ' + ${selectorJson} };
        el.scrollIntoView({ block: 'center' });
        const rect = el.getBoundingClientRect();
        return { x: rect.x + rect.width / 2, y: rect.y + rect.height / 2, tag: el.tagName, text: (el.textContent || '').slice(0, 100) };
      })()`;
      const coordResp = await sendCDP('Runtime.evaluate', {
        expression: js,
        returnByValue: true,
        awaitPromise: true,
      }, sid);
      if (coordResp.error) throw new Error(coordResp.error.message || '定位元素坐标失败');
      const coord = coordResp.result?.result?.value;
      if (!coord || coord.error) {
        sendJson(res, 400, coord || coordResp.result);
        return;
      }
      for (const type of ['mouseMoved', 'mousePressed', 'mouseReleased']) {
        const mouseResp = await sendCDP('Input.dispatchMouseEvent', {
          type, x: coord.x, y: coord.y, button: type === 'mouseMoved' ? 'none' : 'left',
          buttons: type === 'mousePressed' ? 1 : 0, clickCount: type === 'mouseMoved' ? 0 : 1,
        }, sid);
        if (mouseResp.error) throw new Error(mouseResp.error.message || '鼠标事件派发失败');
      }
      sendJson(res, 200, { clicked: true, x: coord.x, y: coord.y, tag: coord.tag, text: coord.text });
    }

    // POST /setFiles?target=xxx — 给 file input 设置本地文件（绕过文件对话框）
    // body: JSON { "selector": "input[type=file]", "files": ["/path/to/file1.png", "/path/to/file2.png"] }
    else if (pathname === '/setFiles') {
      const sid = await ensureSession(q.target);
      let body;
      try { body = JSON.parse(await readBody(req)); }
      catch { sendJson(res, 400, { error: '需要合法的 JSON 请求体' }); return; }
      if (!body || typeof body !== 'object' || !body.selector || !body.files) {
        sendJson(res, 400, { error: '需要 selector 和 files 字段' });
        return;
      }
      const invalidFile = Array.isArray(body.files) && body.files.some(file => {
        if (typeof file !== 'string' || !path.isAbsolute(file)) return true;
        try { return !fs.statSync(file).isFile(); } catch { return true; }
      });
      if (!Array.isArray(body.files) || body.files.length === 0 || body.files.length > 20 || invalidFile) {
        sendJson(res, 400, { error: 'files 必须是 1-20 个存在的绝对文件路径' });
        return;
      }
      // 获取 DOM 节点
      const domEnabled = await sendCDP('DOM.enable', {}, sid);
      if (domEnabled.error) throw new Error(domEnabled.error.message || 'DOM.enable 失败');
      const doc = await sendCDP('DOM.getDocument', {}, sid);
      if (doc.error || !doc.result?.root?.nodeId) throw new Error(doc.error?.message || '读取文档节点失败');
      const node = await sendCDP('DOM.querySelector', {
        nodeId: doc.result.root.nodeId,
        selector: body.selector
      }, sid);
      if (node.error) throw new Error(node.error.message || '查询上传元素失败');
      if (!node.result?.nodeId) {
        sendJson(res, 400, { error: '未找到元素: ' + body.selector });
        return;
      }
      // 设置文件
      const setResp = await sendCDP('DOM.setFileInputFiles', {
        nodeId: node.result.nodeId,
        files: body.files
      }, sid);
      if (setResp.error) throw new Error(setResp.error.message || '设置上传文件失败');
      sendJson(res, 200, { success: true, files: body.files.length });
    }

    // POST /scroll?target=xxx&owner=xxx&y=3000 - 滚动
    else if (pathname === '/scroll') {
      const y = q.y === undefined ? 3000 : Number(q.y);
      if (!Number.isFinite(y)) {
        sendJson(res, 400, { error: '需要有限的数字滚动距离 y' });
        return;
      }
      const sid = await ensureSession(q.target);
      const distance = Math.trunc(Math.abs(y));
      const direction = q.direction || 'down'; // down | up | top | bottom
      let js;
      if (direction === 'top') {
        js = 'window.scrollTo(0, 0); "scrolled to top"';
      } else if (direction === 'bottom') {
        js = 'window.scrollTo(0, document.body.scrollHeight); "scrolled to bottom"';
      } else if (direction === 'up') {
        js = `window.scrollBy(0, -${distance}); "scrolled up ${distance}px"`;
      } else {
        js = `window.scrollBy(0, ${distance}); "scrolled down ${distance}px"`;
      }
      const resp = await sendCDP('Runtime.evaluate', {
        expression: js,
        returnByValue: true,
      }, sid);
      if (resp.error) throw new Error(resp.error.message || '滚动执行失败');
      // 等待懒加载触发
      await new Promise(r => setTimeout(r, 800));
      sendJson(res, 200, { value: resp.result?.result?.value });
    }

    // GET /screenshot?target=xxx&owner=xxx - 返回截图二进制，不接受文件路径
    else if (pathname === '/screenshot') {
      const format = q.format || 'png';
      if (!['png', 'jpeg', 'webp'].includes(format)) {
        sendJson(res, 400, { error: 'format 仅支持 png、jpeg 或 webp' });
        return;
      }
      const sid = await ensureSession(q.target);
      const resp = await sendCDP('Page.captureScreenshot', {
        format,
        quality: format === 'jpeg' ? 80 : undefined,
      }, sid);
      if (resp.error || !resp.result?.data) throw new Error(resp.error?.message || '页面截图失败');
      res.setHeader('Content-Type', 'image/' + format);
      res.end(Buffer.from(resp.result.data, 'base64'));
    }

    // GET /info?target=xxx - 获取页面信息
    else if (pathname === '/info') {
      const sid = await ensureSession(q.target);
      const resp = await sendCDP('Runtime.evaluate', {
        expression: 'JSON.stringify({title: document.title, url: location.href, ready: document.readyState})',
        returnByValue: true,
      }, sid);
      if (resp.error) throw new Error(resp.error.message || '读取页面信息失败');
      let info = {};
      try { info = JSON.parse(resp.result?.result?.value || '{}'); } catch { /* 保持空对象 */ }
      if (typeof info.url === 'string') info.url = redactOutputUrl(info.url);
      sendJson(res, 200, info);
    }

    else sendJson(res, 404, { error: '未知端点' });
  } catch (e) {
    sendJson(res, e instanceof FrameError ? 400 : e.statusCode || 500, { error: redactOutputMessage(e.message) });
  } finally {
    if (operationActive) activeRequests--;
  }
});

async function main() {
  try {
    await new Promise((resolve, reject) => {
      server.once('error', reject);
      server.listen(PORT, '127.0.0.1', resolve);
    });
  } catch (error) {
    if (error.code !== 'EADDRINUSE') throw error;
    for (let i = 0; i < 10; i++) {
      const state = readRuntimeState(PORT);
      try {
        const response = state && await fetch(`http://127.0.0.1:${PORT}/health`, {
          headers: { Authorization: bearerHeader(state.token) }, signal: AbortSignal.timeout(1000),
        });
        if (response?.ok && (await response.json()).status === 'ok') {
          console.log(`[CDP Proxy] 复用端口 ${PORT} 的现有实例`); return;
        }
      } catch { /* the winning process may still be publishing its state */ }
      await delay(100);
    }
    throw new Error(`端口 ${PORT} 已被其它服务占用`);
  }
  writeRuntimeState(PORT, {
    pid: process.pid, port: PORT, token: RUNTIME_TOKEN,
    browser: BROWSER_OVERRIDE, startedAt: STARTED_AT, instanceId: INSTANCE_ID,
  });
  console.log(`[CDP Proxy] 运行在 http://localhost:${PORT}`);

  // 定时清理闲置 tab
  cleanupTimer = setInterval(cleanupIdleTabs, CLEANUP_INTERVAL);
  cleanupTimer.unref();

  process.on('SIGINT', () => shutdownProxy('SIGINT'));
  process.on('SIGTERM', () => shutdownProxy('SIGTERM'));
}

let cleanupTimer = null;
let shuttingDown = false;
async function shutdownProxy(reason) {
  if (shuttingDown) return;
  shuttingDown = true;
  console.log(`[CDP Proxy] ${reason}, cleaning up...`);
  if (cleanupTimer) clearInterval(cleanupTimer);
  const cleanup = await closeAllManagedTabs();
  if (cleanup.retained > 0) {
    console.warn(`[CDP Proxy] ${cleanup.retained} managed tab(s) retained for recovery by the next Proxy instance`);
  }
  connection.close();
  removeRuntimeState(PORT, process.pid);
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(0), 2000).unref();
}

// 防止未捕获异常导致进程崩溃
process.on('uncaughtException', (e) => {
  console.error('[CDP Proxy] 未捕获异常:', e.message);
});
process.on('unhandledRejection', (e) => {
  console.error('[CDP Proxy] 未处理拒绝:', e?.message || e);
});
process.on('exit', () => removeRuntimeState(PORT, process.pid));

main().catch(error => { console.error('[CDP Proxy]', redactOutputMessage(error.message)); process.exitCode = 1; });
