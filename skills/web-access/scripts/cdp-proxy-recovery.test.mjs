import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs';
import net from 'node:net';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';
import { readRuntimeState, runtimeStatePath } from './cdp-runtime.mjs';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));

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

test('浏览器断线退出时保留未清理 tab 的恢复记录', async () => {
  const port = await freePort();
  const managedPath = path.join(os.tmpdir(), `cdp-proxy-managed-tabs-${port}.json`);
  const fakeTarget = 'recovery-target';
  fs.writeFileSync(managedPath, JSON.stringify([
    [fakeTarget, { owner: 'recovery-owner', kind: 'page', lastAccessed: Date.now() }],
  ]), { mode: 0o600 });

  const child = spawn(process.execPath, [path.join(SCRIPT_DIR, 'cdp-proxy.mjs')], {
    env: { ...process.env, CDP_PROXY_PORT: String(port) },
    stdio: 'ignore',
  });

  try {
    for (let i = 0; i < 50 && !readRuntimeState(port); i++) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    assert.ok(readRuntimeState(port), 'Proxy 未写入运行状态');

    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill('SIGTERM');
    await exited;

    const retained = new Map(JSON.parse(fs.readFileSync(managedPath, 'utf8')));
    assert.equal(retained.get(fakeTarget)?.owner, 'recovery-owner');
    assert.equal(fs.existsSync(runtimeStatePath(port)), false);
  } finally {
    if (child.exitCode === null) child.kill('SIGKILL');
    try { fs.unlinkSync(runtimeStatePath(port)); } catch { /* already removed */ }
    try { fs.unlinkSync(managedPath); } catch { /* already removed */ }
  }
});

test('重启恢复的旧 profile-bridge 降级为可回收的 profile-probe', async () => {
  const port = await freePort();
  const managedPath = path.join(os.tmpdir(), `cdp-proxy-managed-tabs-${port}.json`);
  const bridgeTarget = 'stale-bridge-target';
  fs.writeFileSync(managedPath, JSON.stringify([
    [bridgeTarget, { owner: '__web_access_internal_probe__', kind: 'profile-bridge', lastAccessed: Date.now() - 600000 }],
  ]), { mode: 0o600 });

  const child = spawn(process.execPath, [path.join(SCRIPT_DIR, 'cdp-proxy.mjs')], {
    env: { ...process.env, CDP_PROXY_PORT: String(port) },
    stdio: 'ignore',
  });

  try {
    for (let i = 0; i < 50 && !readRuntimeState(port); i++) {
      await new Promise(resolve => setTimeout(resolve, 50));
    }
    const state = readRuntimeState(port);
    assert.ok(state, 'Proxy 未写入运行状态');

    const persisted = new Map(JSON.parse(fs.readFileSync(managedPath, 'utf8')));
    assert.equal(persisted.get(bridgeTarget)?.kind, 'profile-probe');

    const resp = await fetch(`http://127.0.0.1:${port}/info?owner=attacker&target=${bridgeTarget}`, {
      headers: { Authorization: `Bearer ${state.token}` },
    });
    assert.equal(resp.status, 403);

    const exited = new Promise(resolve => child.once('exit', resolve));
    child.kill('SIGTERM');
    await exited;
  } finally {
    if (child.exitCode === null) child.kill('SIGKILL');
    try { fs.unlinkSync(runtimeStatePath(port)); } catch { /* already removed */ }
    try { fs.unlinkSync(managedPath); } catch { /* already removed */ }
  }
});
