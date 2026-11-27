import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { afterEach, test } from 'node:test';
import { copySqliteSnapshot, removeSqliteSnapshot } from './sqlite-snapshot.mjs';

const tempDirs = [];

function assertOwnerOnly(file) {
  // Windows ACLs do not expose POSIX mode semantics consistently. The helper
  // is best-effort there; POSIX platforms must keep snapshots at 0600.
  if (process.platform !== 'win32') {
    assert.equal(fs.statSync(file).mode & 0o777, 0o600);
  }
}

afterEach(() => {
  for (const dir of tempDirs.splice(0)) fs.rmSync(dir, { recursive: true, force: true });
});

test('复制 SQLite 主库和 WAL，忽略瞬态 SHM，并完整清理', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-sqlite-test-'));
  tempDirs.push(dir);
  const source = path.join(dir, 'History');
  const destination = path.join(dir, 'snapshot.sqlite');

  fs.writeFileSync(source, 'main');
  fs.writeFileSync(`${source}-wal`, 'wal');
  fs.writeFileSync(`${source}-shm`, 'shm');
  // Verify the copier also tightens permissions on pre-existing destinations.
  fs.writeFileSync(destination, 'stale-main', { mode: 0o644 });
  fs.writeFileSync(`${destination}-wal`, 'stale-wal', { mode: 0o644 });

  assert.deepEqual(copySqliteSnapshot(source, destination), [
    destination,
    `${destination}-wal`,
  ]);
  assert.equal(fs.readFileSync(destination, 'utf8'), 'main');
  assert.equal(fs.readFileSync(`${destination}-wal`, 'utf8'), 'wal');
  assertOwnerOnly(destination);
  assertOwnerOnly(`${destination}-wal`);
  assert.equal(fs.existsSync(`${destination}-shm`), false);

  // SQLite 打开 WAL 快照时可能重建 SHM，清理函数也必须移除它。
  fs.writeFileSync(`${destination}-shm`, 'rebuilt-shm');

  removeSqliteSnapshot(destination);
  assert.equal(fs.existsSync(destination), false);
  assert.equal(fs.existsSync(`${destination}-wal`), false);
  assert.equal(fs.existsSync(`${destination}-shm`), false);
});

test('没有 WAL/SHM 时仍可复制主库', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'web-access-sqlite-test-'));
  tempDirs.push(dir);
  const source = path.join(dir, 'History');
  const destination = path.join(dir, 'snapshot.sqlite');
  fs.writeFileSync(source, 'main-only');

  assert.deepEqual(copySqliteSnapshot(source, destination), [destination]);
  assert.equal(fs.readFileSync(destination, 'utf8'), 'main-only');
  assertOwnerOnly(destination);
});
