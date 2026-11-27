import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

export function runtimeStatePath(port) {
  return path.join(os.tmpdir(), `web-access-cdp-proxy-${port}.json`);
}

export function createRuntimeToken() {
  return crypto.randomBytes(32).toString('base64url');
}

export function writeRuntimeState(port, state) {
  const target = runtimeStatePath(port);
  const temp = `${target}.${process.pid}.tmp`;
  fs.writeFileSync(temp, JSON.stringify(state), { mode: 0o600 });
  try { fs.chmodSync(temp, 0o600); } catch { /* Windows may ignore POSIX mode */ }
  fs.renameSync(temp, target);
  try { fs.chmodSync(target, 0o600); } catch { /* Windows may ignore POSIX mode */ }
  return target;
}

export function readRuntimeState(port) {
  try {
    const state = JSON.parse(fs.readFileSync(runtimeStatePath(port), 'utf8'));
    if (!state || state.port !== port || typeof state.token !== 'string' || !state.token) return null;
    return state;
  } catch {
    return null;
  }
}

export function removeRuntimeState(port, expectedPid = null) {
  const target = runtimeStatePath(port);
  try {
    if (expectedPid !== null) {
      const current = JSON.parse(fs.readFileSync(target, 'utf8'));
      if (current?.pid !== expectedPid) return false;
    }
    fs.unlinkSync(target);
    return true;
  } catch {
    return false;
  }
}

export function bearerHeader(token) {
  return `Bearer ${token}`;
}

export function tokenMatches(header, token) {
  if (typeof header !== 'string' || !header.startsWith('Bearer ')) return false;
  const supplied = Buffer.from(header.slice(7));
  const expected = Buffer.from(token);
  return supplied.length === expected.length && crypto.timingSafeEqual(supplied, expected);
}

export function normalizeOwner(value) {
  return typeof value === 'string' ? value.trim().slice(0, 128) : '';
}

export function isOwnedTarget(managedTabs, targetId, owner) {
  if (!targetId || !owner) return false;
  return managedTabs.get(targetId)?.owner === owner;
}
