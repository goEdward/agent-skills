import assert from 'node:assert/strict';
import { test } from 'node:test';
import { resolveUnconfiguredBrowser } from './browser-discovery.mjs';

test('Windows 未配置偏好时自动选择 Chrome', () => {
  const result = resolveUnconfiguredBrowser([
    { id: 'edge', label: 'Microsoft Edge' },
    { id: 'chrome', label: 'Chrome' },
  ], 'win32');
  assert.equal(result.kind, 'ok');
  assert.equal(result.source, 'auto');
  assert.equal(result.browser.id, 'chrome');
});

test('Windows 没有 Chrome 时优先选择 Edge', () => {
  const result = resolveUnconfiguredBrowser([
    { id: 'chromium', label: 'Chromium' },
    { id: 'edge', label: 'Microsoft Edge' },
  ], 'win32');
  assert.equal(result.kind, 'ok');
  assert.equal(result.browser.id, 'edge');
});

test('非 Windows 未配置偏好时仍询问用户', () => {
  const result = resolveUnconfiguredBrowser([
    { id: 'chrome', label: 'Chrome' },
  ], 'darwin');
  assert.equal(result.kind, 'ambiguous');
});

test('Windows 未检测到浏览器时返回 empty', () => {
  const result = resolveUnconfiguredBrowser([], 'win32');
  assert.equal(result.kind, 'empty');
});
