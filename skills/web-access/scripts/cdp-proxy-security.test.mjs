import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs';
import http from 'node:http';
import net from 'node:net';
import { after, before, test } from 'node:test';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { promisify } from 'node:util';
import {
  bearerHeader,
  isOwnedTarget,
  readRuntimeState,
  runtimeStatePath,
} from './cdp-runtime.mjs';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
let port;
let child;
let state;
const execFileAsync = promisify(execFile);

function freePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.once('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const selected = server.address().port;
      server.close(() => resolve(selected));
    });
  });
}

function request(pathname, { method = 'GET', headers = {}, body = '' } = {}) {
  return new Promise((resolve, reject) => {
    const req = http.request({
      hostname: '127.0.0.1', port, path: pathname, method,
      // Keep the test server connection short-lived so shutdown can close
      // its listener deterministically after sending the response.
      headers: { Connection: 'close', ...headers },
    }, (res) => {
      let data = '';
      res.on('data', chunk => { data += chunk; });
      res.on('end', () => resolve({ status: res.statusCode, body: data }));
    });
    req.once('error', reject);
    if (body) req.write(body);
    req.end();
  });
}

before(async () => {
  port = await freePort();
  child = spawn(process.execPath, [path.join(SCRIPT_DIR, 'cdp-proxy.mjs')], {
    env: { ...process.env, CDP_PROXY_PORT: String(port) },
    stdio: 'ignore',
  });
  for (let i = 0; i < 50; i++) {
    await new Promise(resolve => setTimeout(resolve, 50));
    state = readRuntimeState(port);
    if (state) return;
  }
  throw new Error('Proxy 未写入运行状态');
});

after(async () => {
  if (child && child.exitCode === null) child.kill('SIGTERM');
  try { fs.unlinkSync(runtimeStatePath(port)); } catch { /* already removed */ }
});

test('拒绝未认证请求', async () => {
  const response = await request('/health');
  assert.equal(response.status, 401);
});

test('认证健康检查不触发浏览器连接', async () => {
  const response = await request('/health', {
    headers: { Authorization: bearerHeader(state.token) },
  });
  assert.equal(response.status, 200);
  assert.equal(JSON.parse(response.body).connected, false);
  assert.equal(JSON.parse(response.body).connectionAttempts, 0);
  assert.match(JSON.parse(response.body).runningRevision, /^[a-f0-9]{12}$/);
});

test('运行状态文件仅当前用户可读写', () => {
  if (process.platform !== 'win32') {
    assert.equal(fs.statSync(runtimeStatePath(port)).mode & 0o777, 0o600);
  }
});

test('拒绝浏览器 Origin 与异常 Host', async () => {
  const origin = await request('/health', {
    headers: { Authorization: bearerHeader(state.token), Origin: 'https://evil.example' },
  });
  assert.equal(origin.status, 403);

  const host = await request('/health', {
    headers: { Authorization: bearerHeader(state.token), Host: 'evil.example' },
  });
  assert.equal(host.status, 403);
});

test('写接口强制 POST', async () => {
  const response = await request('/new?owner=test-owner', {
    headers: { Authorization: bearerHeader(state.token) },
  });
  assert.equal(response.status, 405);
});

test('拒绝缺少 owner 和非受管 target', async () => {
  const headers = { Authorization: bearerHeader(state.token) };
  const missingOwner = await request('/targets', { headers });
  assert.equal(missingOwner.status, 400);

  const unmanaged = await request('/info?owner=test-owner&target=user-tab', { headers });
  assert.equal(unmanaged.status, 403);
});

test('拒绝客户端使用 Proxy 内部保留 owner', async () => {
  const response = await request('/targets?owner=__web_access_internal_probe__', {
    headers: { Authorization: bearerHeader(state.token) },
  });
  assert.equal(response.status, 403);
});

test('owner A 不能操作 owner B 的 target', () => {
  const tabs = new Map([
    ['target-a', { owner: 'owner-a' }],
    ['target-b', { owner: 'owner-b' }],
  ]);
  assert.equal(isOwnedTarget(tabs, 'target-a', 'owner-a'), true);
  assert.equal(isOwnedTarget(tabs, 'target-b', 'owner-a'), false);
});

test('认证客户端可读取运行状态并调用 health', async () => {
  const result = await execFileAsync(process.execPath, [path.join(SCRIPT_DIR, 'cdp-call.mjs'), 'health'], {
    env: { ...process.env, CDP_PROXY_PORT: String(port) },
  });
  assert.equal(JSON.parse(result.stdout).status, 'ok');
});

test('认证 shutdown 精确停止空闲 Proxy', async () => {
  // 注册监听器后再发请求，避免 Proxy 在 setImmediate 中退出得太快，
  // 导致请求返回后才监听 exit 而永久错过事件。
  const exited = new Promise(resolve => child.once('exit', resolve));
  const response = await request('/shutdown', {
    method: 'POST', headers: { Authorization: bearerHeader(state.token) },
    body: JSON.stringify({ instanceId: state.instanceId }),
  });
  assert.equal(response.status, 202);
  await exited;
  assert.equal(fs.existsSync(runtimeStatePath(port)), false);
});
