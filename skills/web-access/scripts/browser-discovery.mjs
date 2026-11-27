// 浏览器 CDP 端口发现 + 选择 - 单一职责模块
// 被 check-deps.mjs 和 cdp-proxy.mjs 共享。
//
// 选择规则（resolution）：
//   1. 调用方传入 override 参数（来自命令行 --browser） → 严格模式，找不到则硬错
//   2. config.env 里 WEB_ACCESS_DEBUG_PORT 设了 → 严格模式，端口不通则硬错
//   3. config.env 里 WEB_ACCESS_BROWSER 设了 → 严格模式，找不到则硬错
//   4. 都没设 → Windows 自动选择 Chrome > Edge > Chromium；其他平台询问用户
//
// 不擅自降级：偏好不可用一律硬错，让用户介入。
// 持久态只有 config.env 一处；override 是单次 spawn 通过命令行参数表达，不读 process.env。

import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { readConfigPreference } from './user-data.mjs';


// 已知支持 chrome://inspect#remote-debugging toggle 的浏览器
// 加新浏览器：只改这里
export function knownBrowsers() {
  const home = os.homedir();
  const localAppData = process.env.LOCALAPPDATA || '';
  switch (os.platform()) {
    case 'darwin':
      return [
        { id: 'chrome',        label: 'Chrome',         devToolsPath: path.join(home, 'Library/Application Support/Google/Chrome/DevToolsActivePort') },
        { id: 'chrome-canary', label: 'Chrome Canary',  devToolsPath: path.join(home, 'Library/Application Support/Google/Chrome Canary/DevToolsActivePort') },
        { id: 'chromium',      label: 'Chromium',       devToolsPath: path.join(home, 'Library/Application Support/Chromium/DevToolsActivePort') },
        { id: 'edge',          label: 'Microsoft Edge', devToolsPath: path.join(home, 'Library/Application Support/Microsoft Edge/DevToolsActivePort') },
      ];
    case 'linux':
      return [
        { id: 'chrome',   label: 'Chrome',         devToolsPath: path.join(home, '.config/google-chrome/DevToolsActivePort') },
        { id: 'chromium', label: 'Chromium',       devToolsPath: path.join(home, '.config/chromium/DevToolsActivePort') },
        { id: 'edge',     label: 'Microsoft Edge', devToolsPath: path.join(home, '.config/microsoft-edge/DevToolsActivePort') },
      ];
    case 'win32':
      return [
        { id: 'chrome',   label: 'Chrome',         devToolsPath: path.join(localAppData, 'Google/Chrome/User Data/DevToolsActivePort') },
        { id: 'chromium', label: 'Chromium',       devToolsPath: path.join(localAppData, 'Chromium/User Data/DevToolsActivePort') },
        { id: 'edge',     label: 'Microsoft Edge', devToolsPath: path.join(localAppData, 'Microsoft/Edge/User Data/DevToolsActivePort') },
      ];
    default:
      return [];
  }
}

// TCP 端口监听检测
// 用 TCP connect 而非 WebSocket，避免触发浏览器的远程调试授权弹窗。
export function checkPort(port, host = '127.0.0.1', timeoutMs = 2000) {
  return new Promise((resolve) => {
    const socket = net.createConnection(port, host);
    const timer = setTimeout(() => { socket.destroy(); resolve(false); }, timeoutMs);
    socket.once('connect', () => { clearTimeout(timer); socket.destroy(); resolve(true); });
    socket.once('error',   () => { clearTimeout(timer); resolve(false); });
  });
}

// 读 config.env 文件（不写入 process.env，分清来源）
// 格式：KEY=VALUE，# 开头是注释
function readConfig() {
  return Object.fromEntries(['WEB_ACCESS_BROWSER', 'WEB_ACCESS_DEBUG_PORT']
    .map(key => [key, readConfigPreference(key)]));
}

function parseConfiguredPort(rawPort) {
  if (!rawPort) return null;
  const port = parseInt(rawPort, 10);
  return port > 0 && port < 65536 ? port : null;
}

// 返回所有开了 toggle 且端口活的浏览器
async function detectAll() {
  const result = [];
  for (const browser of knownBrowsers()) {
    let content;
    try { content = fs.readFileSync(browser.devToolsPath, 'utf8'); }
    catch { continue; }
    const lines = content.trim().split(/\r?\n/).filter(Boolean);
    const port = parseInt(lines[0], 10);
    if (!(port > 0 && port < 65536)) continue;
    if (!(await checkPort(port))) continue;
    result.push({ ...browser, port, wsPath: lines[1] || null });
  }
  return result;
}

async function detectConfiguredPort(port, detected) {
  const matchedBrowser = detected.find((browser) => browser.port === port);
  if (matchedBrowser) return matchedBrowser;
  if (!(await checkPort(port))) return null;
  return {
    id: 'unknown',
    label: `自定义调试端口 ${port}`,
    port,
    wsPath: null,
  };
}

// 决策入口
// 参数：override — 调用方解析自命令行 --browser 的值（null 表示未传）
// 返回 { kind, browser?, source?, detected, configured, configuredPort?, override? }
//   kind ∈ 'ok' | 'ambiguous' | 'mismatch' | 'port_mismatch' | 'empty'
//   source ∈ 'override' | 'preference' | 'port-preference' | 'auto' | undefined
//   ambiguous = 非 Windows 没设偏好 + 至少一个浏览器开了 toggle，需问用户
//   mismatch  = override/配偏好设了但未检测到对应 toggle，硬错
//   port_mismatch = 配了固定调试端口，但端口未连通，硬错
//   empty     = 0 浏览器开 toggle 且未设偏好/override
export function resolveUnconfiguredBrowser(detected, platform = os.platform()) {
  if (detected.length === 0) return { kind: 'empty' };
  if (platform === 'win32') {
    const priority = ['chrome', 'edge', 'chromium'];
    const browser = priority.map(id => detected.find(item => item.id === id)).find(Boolean) || detected[0];
    return { kind: 'ok', browser, source: 'auto' };
  }
  return { kind: 'ambiguous' };
}

export async function selectBrowser(override = null) {
  const detected = await detectAll();
  const config = readConfig();
  const configured = config.WEB_ACCESS_BROWSER || null;
  const configuredPort = parseConfiguredPort(config.WEB_ACCESS_DEBUG_PORT || null);

  // 1. 命令行 override（最高优先，单次有效）
  if (override) {
    const match = detected.find(b => b.id === override);
    if (match) return { kind: 'ok', browser: match, source: 'override', detected, configured, configuredPort, override };
    return { kind: 'mismatch', source: 'override', detected, configured, configuredPort, override };
  }

  // 2. config.env fixed port（持久）
  if (configuredPort) {
    const match = await detectConfiguredPort(configuredPort, detected);
    if (match) return { kind: 'ok', browser: match, source: 'port-preference', detected, configured, configuredPort };
    return { kind: 'port_mismatch', detected, configured, configuredPort };
  }

  // 3. config.env preference（持久）
  if (configured) {
    const match = detected.find(b => b.id === configured);
    if (match) return { kind: 'ok', browser: match, source: 'preference', detected, configured, configuredPort };
    return { kind: 'mismatch', source: 'preference', detected, configured, configuredPort };
  }

  // 4. 无偏好 —— Windows 自动选择，其他平台询问用户
  return {
    ...resolveUnconfiguredBrowser(detected),
    detected,
    configured,
    configuredPort,
  };
}

// 兜底：扫描常用固定端口
// 适用场景：用户手动 --remote-debugging-port=9222 启动浏览器，
// 此时 DevToolsActivePort 可能不在默认 user-data-dir。
export async function findFallbackPort() {
  for (const port of [9222, 9229, 9333]) {
    if (await checkPort(port)) return port;
  }
  return null;
}
