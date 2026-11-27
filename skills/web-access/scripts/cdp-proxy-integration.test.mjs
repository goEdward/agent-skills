import assert from 'node:assert/strict';
import { execFile, spawn } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import http from 'node:http';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { promisify } from 'node:util';
import { test } from 'node:test';
import { bearerHeader, readRuntimeState, runtimeStatePath } from './cdp-runtime.mjs';
const exec = promisify(execFile);
const SCRIPTS = path.dirname(fileURLToPath(import.meta.url));
const PRELOAD = `
import fs from 'node:fs';
import path from 'node:path';
const root = process.env.TEST_ROOT;
const record = data => fs.appendFileSync(path.join(root, 'commands.jsonl'), JSON.stringify(data) + '\\n');
class FakeWebSocket extends EventTarget {
  constructor() {
    super(); this.readyState = 0; this.count = 0;
    this.pages = new Map([
      ['user-work', { targetId: 'user-work', type: 'page', url: 'https://work.example/', browserContextId: 'work' }],
      ['user-target', { targetId: 'user-target', type: 'page', url: 'https://target.example/', browserContextId: 'target' }],
    ]);
    queueMicrotask(() => { this.readyState = 1; this.dispatchEvent(new Event('open')); });
  }
  close() { this.readyState = 3; this.dispatchEvent(new Event('close')); }
  send(text) { void this.respond(JSON.parse(text)); }
  async respond(message) {
    const p = message.params || {};
    record(message);
    if (message.method === 'Target.getTargets' && fs.existsSync(path.join(root, 'hold-targets'))) {
      while (!fs.existsSync(path.join(root, 'release-targets'))) await new Promise(resolve => setTimeout(resolve, 5));
    }
    const target = this.pages.get((message.sessionId || '').replace(/^session-/, ''));
    let result = {}, error;
    switch (message.method) {
      case 'Target.getTargets': result = { targetInfos: [...this.pages.values(), { targetId: 'child-frame', type: 'iframe', url: 'https://third.example/reset-password/frame-secret' }] }; break;
      case 'Target.createTarget': {
        if (p.url === 'about:blank' && fs.existsSync(path.join(root, 'stale-context-once'))) {
          fs.unlinkSync(path.join(root, 'stale-context-once')); error = { message: 'browser context target not found' }; break;
        }
        if (p.browserContextId && fs.existsSync(path.join(root, 'reject-context-create'))) {
          error = { message: 'failed to find browser context' }; break;
        }
        const targetId = 'created-' + (++this.count);
        const page = { targetId, type: 'page', title: 'Task page', url: p.url, browserContextId: p.browserContextId || 'work', nav: 0 };
        this.pages.set(targetId, page); result = { targetId }; break;
      }
      case 'Target.getTargetInfo': result = { targetInfo: this.pages.get(p.targetId) }; break;
      case 'Target.closeTarget':
        if (fs.existsSync(path.join(root, 'fail-close')) && this.pages.get(p.targetId)?.url.startsWith('https:')) error = { message: 'simulated close failure' };
        else { this.pages.delete(p.targetId); result = { success: true }; } break;
      case 'Target.attachToTarget': result = { sessionId: 'session-' + p.targetId }; break;
      case 'Page.navigate': target.url = p.url; target.nav++; result = { loaderId: 'loader-' + target.nav }; break;
      case 'Page.getFrameTree': result = { frameTree: { frame: { id: 'root' } } }; break;
      case 'Page.createIsolatedWorld': result = { executionContextId: 7 }; break;
      case 'Runtime.getProperties': result = { result: [{ name: '0', value: { objectId: 'iframe-element' } }] }; break;
      case 'DOM.describeNode': result = { node: p.objectId === 'button' ? {} : { frameId: 'child-frame', attributes: ['id', 'embedded', 'src', '/oauth/callback?token=frame-secret'] } }; break;
      case 'DOM.getBoxModel': result = { model: { content: [50, 60, 350, 60, 350, 310, 50, 310] } }; break;
      case 'Runtime.evaluate': {
        const e = p.expression;
        if (e.includes('querySelectorAll')) result = { result: { objectId: 'node-list' } };
        else if (e.includes('window.open(')) {
          const targetId = 'opened-' + (++this.count);
          this.pages.set(targetId, { targetId, type: 'page', title: 'Task page', url: 'about:blank', browserContextId: target.browserContextId, openerId: target.targetId, nav: 0 });
          result = { result: { value: true } };
        }
        else if (e.includes('elementFromPoint')) result = { result: { objectId: message.sessionId === 'session-child-frame' ? 'button' : 'iframe-element' } };
        else if (e.includes('innerWidth')) result = { result: { value: [300, 250] } };
        else if (p.contextId) result = { result: { value: 'iframe-content' } };
        else if (e.includes('document.body')) result = { result: { value: 'Profile Path\\t/fake/Chrome/Profile ' + (target.browserContextId === 'target' ? '8' : '1') } };
        else if (e.includes('JSON.stringify')) result = { result: { value: JSON.stringify({ title: 'Task page', url: target.url, ready: 'complete', timeOrigin: target.nav || 0 }) } };
        else result = { result: { value: 'Task page' } }; break;
      }
    }
    this.dispatchEvent(new MessageEvent('message', { data: JSON.stringify({ id: message.id, ...(error ? { error } : { result }) }) }));
  }
}
globalThis.WebSocket = FakeWebSocket;
`;

async function fixture(t) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-integrated-'));
  const scripts = path.join(root, 'scripts');
  fs.mkdirSync(scripts);
  for (const file of fs.readdirSync(SCRIPTS).filter(file => file.endsWith('.mjs') && !file.endsWith('.test.mjs'))) fs.copyFileSync(path.join(SCRIPTS, file), path.join(scripts, file));
  fs.writeFileSync(path.join(scripts, 'preload.mjs'), PRELOAD);
  fs.writeFileSync(path.join(scripts, 'browser-discovery.mjs'), `
    import fs from 'node:fs'; import path from 'node:path';
    export async function selectBrowser() {
      fs.appendFileSync(path.join(process.env.TEST_ROOT, 'discoveries'), '1');
      while (fs.existsSync(path.join(process.env.TEST_ROOT, 'hold-discovery')) && !fs.existsSync(path.join(process.env.TEST_ROOT, 'release-discovery'))) await new Promise(r => setTimeout(r, 5));
      return { kind: 'ok', source: 'preference', browser: { id: 'chrome', label: 'Chrome', port: 9222, wsPath: '/fake' } };
    }
    export function findFallbackPort() { throw new Error('must not fall back'); }
    export function knownBrowsers() { return [{ id: 'chrome', label: 'Chrome' }]; }
  `);
  fs.mkdirSync(path.join(root, '.web-access'));
  fs.writeFileSync(path.join(root, '.web-access', 'config.env'), 'WEB_ACCESS_BROWSER=chrome\nWEB_ACCESS_BROWSER_PROFILE=Profile 8\nWEB_ACCESS_CHROME_ANCHOR_URLS=target.example\n');
  const server = http.createServer();
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  const port = server.address().port;
  await new Promise(resolve => server.close(resolve));
  const children = [];
  const env = { ...process.env, HOME: root, USERPROFILE: root, CDP_PROXY_PORT: String(port), TEST_ROOT: root };
  t.after(async () => {
    const current = readRuntimeState(port);
    if (current && !children.some(child => child.pid === current.pid)) {
      try { process.kill(current.pid, 'SIGKILL'); } catch { /* already exited */ }
    }
    for (const child of children) if (child.exitCode === null && child.signalCode === null) {
      const exited = new Promise(resolve => child.once('close', resolve)); child.kill('SIGKILL'); await exited;
    }
    for (const file of [runtimeStatePath(port), path.join(os.tmpdir(), 'cdp-proxy-managed-tabs-' + port + '.json')]) fs.rmSync(file, { force: true });
    fs.rmSync(root, { recursive: true, force: true });
  });
  function spawnProxy() {
    const child = spawn(process.execPath, ['--import', path.join(scripts, 'preload.mjs'), path.join(scripts, 'cdp-proxy.mjs')], { env, stdio: 'ignore' });
    children.push(child); return child;
  }
  const wait = async predicate => {
    const until = Date.now() + 4000;
    while (Date.now() < until) { if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 10)); }
    throw new Error('fixture did not reach the expected state');
  };
  const child = spawnProxy();
  await wait(() => readRuntimeState(port));
  const request = async (endpoint, method = 'GET', body) => {
    const state = readRuntimeState(port);
    const response = await fetch(`http://127.0.0.1:${port}${endpoint}`, { method, headers: { Authorization: bearerHeader(state.token), Connection: 'close' }, body: body === undefined ? undefined : typeof body === 'string' ? body : JSON.stringify(body), signal: AbortSignal.timeout(5000) });
    return { status: response.status, contentType: response.headers.get('content-type') || '', data: await response.json() };
  };
  return { root, port, env, child, request, wait, spawnProxy,
    flag: name => fs.writeFileSync(path.join(root, name), ''),
    commands: () => fs.existsSync(path.join(root, 'commands.jsonl')) ? fs.readFileSync(path.join(root, 'commands.jsonl'), 'utf8').trim().split('\n').filter(Boolean).map(JSON.parse) : [],
    cli: (file, ...args) => exec(process.execPath, [path.join(scripts, file), ...args], { env }),
  };
}

test('integrated Profile 8, owner isolation, URL privacy, iframe client and cleanup recovery', async t => {
  const f = await fixture(t);
  const owner = 'integration-' + crypto.randomUUID();
  const page = await f.request('/new?owner=' + owner + '&url=' + encodeURIComponent('https://example.com/a?token=task-secret'), 'POST');
  assert.equal(page.status, 200);
  assert.equal(page.data.loaded, true);
  assert.ok(!JSON.stringify(page.data).includes('task-secret'));
  const id = page.data.targetId;
  const list = await f.request('/targets?owner=' + owner);
  assert.deepEqual(list.data.map(p => p.targetId), [id]);
  assert.ok(!JSON.stringify(list).includes('task-secret'));
  for (const endpoint of ['/frames', '/eval', '/clickXY']) {
    const response = await f.request(endpoint + '?owner=other-owner&target=' + id, endpoint === '/frames' ? 'GET' : 'POST', endpoint === '/eval' ? 'document.title' : undefined);
    assert.equal(response.status, 403);
  }
  const frames = JSON.parse((await f.cli('cdp-call.mjs', 'frames', '--owner', owner, '--target', id)).stdout);
  assert.equal(frames[0].frameId, 'child-frame');
  assert.ok(!JSON.stringify(frames).includes('frame-secret'));
  const evaluated = JSON.parse((await f.cli('cdp-call.mjs', 'eval', '--owner', owner, '--target', id, '--frame', 'child-frame', '--expr', 'document.title')).stdout);
  assert.equal(evaluated.value, 'iframe-content');
  assert.equal((await f.request(`/eval?owner=${owner}&target=${id}&frame=user-work`, 'POST', 'document.title')).status, 400);
  const clicked = JSON.parse((await f.cli('cdp-call.mjs', 'click-xy', '--owner', owner, '--target', id, '--x', '120', '--y', '90')).stdout);
  assert.equal(clicked.clicked, true);
  assert.ok(f.commands().some(c => c.method === 'Input.dispatchMouseEvent' && c.sessionId === 'session-child-frame' && c.params.x === 70 && c.params.y === 30));
  assert.equal((await f.request('/shutdown', 'POST', { instanceId: readRuntimeState(f.port).instanceId })).status, 409);
  f.flag('fail-close');
  const retained = await f.request('/close-owned?owner=' + owner, 'POST');
  assert.equal(retained.data.retained, 1);
  assert.equal((await f.request('/targets?owner=' + owner)).data.length, 1);
  fs.unlinkSync(path.join(f.root, 'fail-close'));
  assert.equal((await f.request('/close-owned?owner=' + owner, 'POST')).data.retained, 0);
  assert.deepEqual((await f.request('/targets?owner=' + owner)).data, []);
  const calls = f.commands();
  assert.ok(calls.filter(c => c.method === 'Target.createTarget').every(c => c.params.background === true));
  assert.ok(calls.some(c => c.method === 'Target.createTarget' && c.params.browserContextId === 'target'));
  assert.ok(!calls.some(c => ['Target.closeTarget', 'Target.attachToTarget'].includes(c.method) && c.params.targetId?.startsWith('user-')));
  assert.equal(JSON.parse((await f.cli('proxy-maintenance.mjs', 'stop')).stdout).status, 'stopped');
});

test('concurrent HTTP requests share discovery; busy maintenance is rejected', async t => {
  const f = await fixture(t);
  f.flag('hold-discovery');
  const first = f.request('/connect', 'POST');
  const second = f.request('/connect', 'POST');
  await f.wait(() => fs.existsSync(path.join(f.root, 'discoveries')));
  const health = await f.request('/health');
  assert.equal(health.data.connectionState, 'discovering');
  assert.equal(health.data.activeRequests, 2);
  assert.equal((await f.request('/shutdown', 'POST', { instanceId: health.data.instanceId })).status, 409);
  f.flag('release-discovery');
  assert.ok((await Promise.all([first, second])).every(r => r.status === 200));
  assert.equal(fs.readFileSync(path.join(f.root, 'discoveries'), 'utf8'), '1');
  f.flag('hold-targets');
  const pending = f.request('/probe', 'POST');
  await f.wait(() => f.commands().some(c => c.method === 'Target.getTargets'));
  const busy = await f.request('/health');
  assert.ok(busy.data.pendingCommands > 0);
  await assert.rejects(f.cli('proxy-maintenance.mjs', 'stop'), /拒绝停止/);
  f.flag('release-targets');
  await pending;
  assert.equal((await f.request('/shutdown', 'POST', { instanceId: 'wrong' })).status, 409);
  assert.equal(JSON.parse((await f.cli('proxy-maintenance.mjs', 'stop')).stdout).status, 'stopped');
});

test('a second proxy process reuses the authenticated listener without owning its state', async t => {
  const f = await fixture(t);
  const original = readRuntimeState(f.port);
  const duplicate = f.spawnProxy();
  const exited = await new Promise(resolve => duplicate.once('close', resolve));
  assert.equal(exited, 0);
  assert.equal(readRuntimeState(f.port).pid, original.pid);
  assert.equal((await f.request('/health')).data.connectionAttempts, 0);
});

test('quick check reuses healthy service without another discovery or Profile probe', async t => {
  const f = await fixture(t);
  await f.request('/connect', 'POST');
  const checked = await f.cli('check-deps.mjs', '--quick');
  assert.match(checked.stdout, /proxy: ready/);
  assert.equal(fs.readFileSync(path.join(f.root, 'discoveries'), 'utf8'), '1');
  assert.ok(!f.commands().some(c => c.method === 'Target.createTarget'));
  assert.equal(fs.readFileSync(path.join(f.root, '.web-access', 'config.env'), 'utf8').includes('Profile 8'), true);
});

test('static site-pattern lookup uses domain boundaries and personal overrides without CDP', async t => {
  const f = await fixture(t);
  const bundled = path.join(f.root, 'references', 'site-patterns');
  fs.mkdirSync(bundled, { recursive: true });
  fs.writeFileSync(path.join(bundled, 'example.com.md'), '---\ndomain: example.com\naliases: [示例]\n---\nbundled pattern\n');
  assert.equal((await f.cli('match-site.mjs', 'https://notexample.com/')).stdout, '');
  assert.equal((await f.cli('match-site.mjs', 'https://example.com.evil/')).stdout, '');
  assert.match((await f.cli('match-site.mjs', 'https://www.example.com/')).stdout, /bundled pattern/);
  const personal = path.join(f.root, '.web-access', 'site-patterns');
  fs.mkdirSync(personal);
  fs.writeFileSync(path.join(personal, 'example.com.md'), '---\ndomain: example.com\naliases: [示例]\n---\npersonal pattern\n');
  assert.match((await f.cli('match-site.mjs', '示例')).stdout, /personal pattern/);
  assert.equal((await f.request('/health')).data.connectionAttempts, 0);
  assert.ok(!fs.existsSync(path.join(f.root, '.web-access', '.migrated')));
});

test('wrong configured Profile fails closed and does not navigate a task page', async t => {
  const f = await fixture(t);
  fs.writeFileSync(path.join(f.root, '.web-access', 'config.env'), 'WEB_ACCESS_BROWSER=chrome\nWEB_ACCESS_BROWSER_PROFILE=Profile 80\n');
  const result = await f.request('/new?owner=wrong-profile&url=https://example.com', 'POST');
  assert.equal(result.status, 500);
  assert.match(result.data.error, /未加载/);
  assert.deepEqual((await f.request('/targets?owner=wrong-profile')).data, []);
  assert.ok(!f.commands().some(c => c.method === 'Page.navigate' && c.params.url.startsWith('https:')));
});

test('a stale Profile context is relocated and retried once without switching Profile', async t => {
  const f = await fixture(t);
  f.flag('stale-context-once');
  const result = await f.request('/new?owner=recovery-owner&url=https://example.com', 'POST');
  assert.equal(result.status, 200);
  assert.equal(result.data.loaded, true);
  const creates = f.commands().filter(c => c.method === 'Target.createTarget' && c.params.url === 'about:blank');
  assert.equal(creates.length, 2);
  assert.ok(creates.every(c => c.params.browserContextId === 'target'));
  assert.equal((await f.request('/close-owned?owner=recovery-owner', 'POST')).data.retained, 0);
});

test('a failed user-data migration neither overwrites old preferences nor connects', async t => {
  const f = await fixture(t);
  fs.rmSync(path.join(f.root, '.web-access'), { recursive: true });
  fs.writeFileSync(path.join(f.root, '.web-access'), 'blocked target');
  const config = 'WEB_ACCESS_BROWSER=chrome\nWEB_ACCESS_CHROME_PROFILE=Profile 8\n';
  fs.writeFileSync(path.join(f.root, 'config.env'), config);
  await assert.rejects(f.cli('check-deps.mjs', '--quick'));
  assert.equal(fs.readFileSync(path.join(f.root, 'config.env'), 'utf8'), config);
  assert.equal(fs.readFileSync(path.join(f.root, '.web-access'), 'utf8'), 'blocked target');
  assert.equal((await f.request('/health')).data.connectionAttempts, 0);
});

test('authenticated maintenance restarts one idle instance and preserves its browser', async t => {
  const f = await fixture(t);
  await f.request('/connect', 'POST');
  const before = (await f.request('/health')).data;
  const preload = pathToFileURL(path.join(f.root, 'scripts', 'preload.mjs')).href;
  const result = await exec(process.execPath, [path.join(f.root, 'scripts', 'proxy-maintenance.mjs'), 'restart'], {
    env: { ...f.env, NODE_OPTIONS: `${process.env.NODE_OPTIONS || ''} --import=${preload}` },
  });
  const after = JSON.parse(result.stdout);
  assert.equal(after.status, 'ready');
  assert.notEqual(after.instanceId, before.instanceId);
  assert.equal(after.browser.id, 'chrome');
  assert.equal(after.connectionState, 'connected');
  assert.equal(JSON.parse((await f.cli('proxy-maintenance.mjs', 'stop')).stdout).status, 'stopped');
});

test('endpoint input validation, JSON headers, and cdp-call --key=value handling', async t => {
  const f = await fixture(t);
  const owner = 'validate-' + crypto.randomUUID();
  const page = await f.request('/new?owner=' + owner + '&url=about:blank', 'POST');
  assert.equal(page.status, 200);
  assert.match(page.contentType, /^application\/json\b/);
  const id = page.data.targetId;

  const badScroll = await f.request(`/scroll?owner=${owner}&target=${id}&y=not-a-number`, 'POST');
  assert.equal(badScroll.status, 400);

  const badFormat = await f.request(`/screenshot?owner=${owner}&target=${id}&format=svg`, 'GET');
  assert.equal(badFormat.status, 400);

  const badJson = await f.request(`/setFiles?owner=${owner}&target=${id}`, 'POST', '{bad-json');
  assert.equal(badJson.status, 400);

  const infoViaEq = JSON.parse((await f.cli('cdp-call.mjs', 'info', `--owner=${owner}`, `--target=${id}`)).stdout);
  assert.equal(infoViaEq.title, 'Task page');

  await assert.rejects(f.cli('cdp-call.mjs', 'screenshot', `--owner=${owner}`, `--target=${id}`), /--output/);
  assert.ok(!f.commands().some(c => c.method === 'Page.captureScreenshot'));

  assert.equal((await f.request('/close-owned?owner=' + owner, 'POST')).data.retained, 0);
});

test('Chrome 151+ rejected browserContextId falls back to verified context bridge without switching Profile', async t => {
  const f = await fixture(t);
  fs.writeFileSync(
    path.join(f.root, '.web-access', 'config.env'),
    'WEB_ACCESS_BROWSER=chrome\nWEB_ACCESS_BROWSER_PROFILE=Profile 8\nWEB_ACCESS_CHROME_ANCHOR_URLS=target.example\nWEB_ACCESS_ALLOW_FOREGROUND_FALLBACK=true\n'
  );
  f.flag('reject-context-create');
  const owner = 'bridge-fallback-' + crypto.randomUUID();
  const page = await f.request('/new?owner=' + owner + '&url=' + encodeURIComponent('https://example.com/ok'), 'POST');
  assert.equal(page.status, 200);
  assert.equal(page.data.loaded, true);
  assert.match(page.data.targetId, /^opened-/);
  assert.equal((await f.request('/close-owned?owner=' + owner, 'POST')).data.retained, 0);
});
