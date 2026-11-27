import assert from 'node:assert/strict';
import { execFileSync, spawnSync } from 'node:child_process';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { test } from 'node:test';
import { fileURLToPath } from 'node:url';

const SCRIPT = path.join(path.dirname(fileURLToPath(import.meta.url)), 'find-url.mjs');

function chromeBaseDir(home) {
  if (process.platform === 'darwin') return path.join(home, 'Library/Application Support/Google/Chrome');
  if (process.platform === 'linux') return path.join(home, '.config/google-chrome');
  return path.join(home, 'Google/Chrome/User Data');
}

function makeTestEnv(home) {
  return {
    ...process.env,
    HOME: home,
    USERPROFILE: home,
    ...(process.platform === 'win32' ? { LOCALAPPDATA: home } : {}),
  };
}

test('find-url --help 输出完整示例列表', () => {
  const res = spawnSync(process.execPath, [SCRIPT, '--help'], { encoding: 'utf8' });
  assert.equal(res.status, 0);
  assert.match(res.stderr, /--browser <id>.*chrome-canary/);
  assert.match(res.stderr, /--browser edge\s+# 只查 Edge/);
});

test('find-url 正确处理含换行/制表符/NULL 标题、LIKE 通配符与 URL 脱敏', (t) => {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-find-url-'));
  t.after(() => fs.rmSync(home, { recursive: true, force: true }));

  const base = chromeBaseDir(home);
  const profileDir = path.join(base, 'Profile 8');
  fs.mkdirSync(profileDir, { recursive: true });
  fs.writeFileSync(path.join(base, 'Local State'), JSON.stringify({
    profile: { info_cache: { 'Profile 8': { name: 'Personal' } } },
  }));
  fs.writeFileSync(path.join(profileDir, 'Bookmarks'), JSON.stringify({
    roots: {
      bookmark_bar: {
        type: 'folder',
        name: '书签栏',
        children: [
          { type: 'url', name: '内部|工具', url: 'https://internal.example/app?token=bm-secret&tab=1' },
        ],
      },
    },
  }));

  const dbPath = path.join(profileDir, 'History');
  const nowWebkitUs = BigInt(Date.now()) * 1000n + 11644473600000000n;
  execFileSync('sqlite3', [dbPath, `
    CREATE TABLE urls (id INTEGER PRIMARY KEY, url LONGVARCHAR, title LONGVARCHAR, visit_count INTEGER, last_visit_time INTEGER);
    INSERT INTO urls (url, title, visit_count, last_visit_time) VALUES
      ('https://notes.example/foo_bar?access_token=hist-secret&p=2', '多行' || char(10) || '制表' || char(9) || '标题', 5, ${nowWebkitUs}),
      ('https://notes.example/fooxbar', '不应命中下划线通配符', 2, ${nowWebkitUs - 1000000n}),
      ('https://nulltitle.example/alpha_only', NULL, 1, ${nowWebkitUs - 2000000n});
  `]);

  const env = makeTestEnv(home);

  // 1) 下划线字面量匹配，不误匹配 fooxbar，且换行/制表符不会拆裂输出，token 被脱敏
  const out = execFileSync(process.execPath, [SCRIPT, 'foo_bar', '--only', 'history', '--browser', 'chrome'], {
    env, encoding: 'utf8',
  });
  assert.match(out, /\[历史\] 1 条/);
  assert.match(out, /多行 制表 标题/);
  assert.match(out, /access_token=%5BREDACTED%5D/);
  assert.ok(!out.includes('hist-secret'));
  assert.ok(!out.includes('fooxbar'));

  // 2) title 为 NULL 时仍可按 URL 命中
  const nullOut = execFileSync(process.execPath, [SCRIPT, 'alpha_only', '--only', 'history', '--browser', 'chrome'], {
    env, encoding: 'utf8',
  });
  assert.match(nullOut, /\[历史\] 1 条/);
  assert.match(nullOut, /\(无标题\)/);
  assert.match(nullOut, /nulltitle\.example\/alpha_only/);

  // 3) 书签脱敏与竖线替换
  const bmOut = execFileSync(process.execPath, [SCRIPT, '内部', '--only', 'bookmarks', '--browser', 'chrome'], {
    env, encoding: 'utf8',
  });
  assert.match(bmOut, /\[书签\] 1 条/);
  assert.match(bmOut, /内部│工具/);
  assert.ok(!bmOut.includes('bm-secret'));
});
