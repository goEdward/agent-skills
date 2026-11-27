#!/usr/bin/env node

import fs from 'node:fs';
import { bearerHeader, readRuntimeState } from './cdp-runtime.mjs';

const PORT = Number(process.env.CDP_PROXY_PORT || 3456);

function parseArgs(argv) {
  const [command, ...rest] = argv;
  const opts = {};
  for (let i = 0; i < rest.length; i++) {
    const arg = rest[i];
    if (!arg.startsWith('--')) continue;
    const eq = arg.indexOf('=');
    if (eq > 2) {
      opts[arg.slice(2, eq)] = arg.slice(eq + 1);
      continue;
    }
    const key = arg.slice(2);
    if (key === 'stdin') opts[key] = true;
    else if (rest[i + 1] !== undefined) opts[key] = rest[++i];
  }
  return { command, opts };
}

function usage() {
  console.error(`用法：cdp-call.mjs <command> [options]
命令：health, connect, probe, targets, new, close, close-owned, navigate, back,
      info, eval, frames, click, click-at, click-xy, set-files, scroll, screenshot, shutdown
通用：--owner OWNER --target ID；iframe：--frame FRAME_ID；坐标：--x X --y Y
内容：eval 使用 --expr CODE 或 --stdin；click/click-at 使用 --selector CSS
截图：screenshot 使用 --output FILE；上传：set-files 使用 --json JSON`);
}

async function stdinText() {
  let data = '';
  for await (const chunk of process.stdin) data += chunk;
  return data;
}

function specFor(command, opts) {
  const query = new URLSearchParams();
  for (const key of ['owner', 'target', 'url', 'frame', 'x', 'y', 'direction', 'format', 'resetContext']) {
    if (opts[key] !== undefined) query.set(key, opts[key]);
  }
  const specs = {
    health: ['GET', '/health'],
    connect: ['POST', '/connect'],
    probe: ['POST', '/probe'],
    targets: ['GET', '/targets'],
    new: ['POST', '/new'],
    close: ['POST', '/close'],
    'close-owned': ['POST', '/close-owned'],
    navigate: ['POST', '/navigate'],
    back: ['POST', '/back'],
    info: ['GET', '/info'],
    eval: ['POST', '/eval'],
    frames: ['GET', '/frames'],
    'click-xy': ['POST', '/clickXY'],
    click: ['POST', '/click'],
    'click-at': ['POST', '/clickAt'],
    'set-files': ['POST', '/setFiles'],
    scroll: ['POST', '/scroll'],
    screenshot: ['GET', '/screenshot'],
    shutdown: ['POST', '/shutdown'],
  };
  const spec = specs[command];
  if (!spec) return null;
  return { method: spec[0], path: `${spec[1]}${query.size ? `?${query}` : ''}` };
}

async function main() {
  const { command, opts } = parseArgs(process.argv.slice(2));
  const spec = specFor(command, opts);
  if (!spec) {
    usage();
    process.exit(2);
  }

  if (command === 'screenshot' && !opts.output) {
    throw new Error('screenshot 必须提供 --output FILE');
  }

  const state = readRuntimeState(PORT);
  if (!state) throw new Error(`未找到端口 ${PORT} 的 Proxy 运行状态；请先运行 check-deps.mjs`);

  let body;
  let contentType = 'text/plain; charset=utf-8';
  if (command === 'eval') body = opts.stdin ? await stdinText() : opts.expr;
  if (command === 'click' || command === 'click-at') body = opts.selector;
  if (command === 'set-files') {
    body = opts.json;
    contentType = 'application/json; charset=utf-8';
  }
  if (command === 'shutdown') {
    const health = await fetch(`http://127.0.0.1:${PORT}/health`, {
      headers: { Authorization: bearerHeader(state.token) }, signal: AbortSignal.timeout(3000),
    });
    const info = await health.json();
    if (!health.ok || !info.instanceId) throw new Error('旧实例没有安全维护接口；请使用维护命令查看迁移说明');
    body = JSON.stringify({ instanceId: info.instanceId });
    contentType = 'application/json; charset=utf-8';
  }
  if (['eval', 'click', 'click-at', 'set-files'].includes(command) && !body) {
    throw new Error(`${command} 缺少请求内容`);
  }

  const response = await fetch(`http://127.0.0.1:${PORT}${spec.path}`, {
    method: spec.method,
    headers: {
      Authorization: bearerHeader(state.token),
      ...(body !== undefined ? { 'Content-Type': contentType } : {}),
    },
    body,
    signal: AbortSignal.timeout(35000),
  });

  if (!response.ok) {
    const message = await response.text();
    throw new Error(`Proxy 返回 ${response.status}: ${message}`);
  }

  if (command === 'screenshot') {
    const data = Buffer.from(await response.arrayBuffer());
    fs.writeFileSync(opts.output, data);
    console.log(JSON.stringify({ saved: opts.output, bytes: data.length }));
    return;
  }

  process.stdout.write(await response.text());
  process.stdout.write('\n');
}

main().catch((error) => {
  console.error(error.message);
  process.exit(1);
});
