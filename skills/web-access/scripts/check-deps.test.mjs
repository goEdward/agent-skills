import test from 'node:test';
import assert from 'node:assert/strict';
import { buildWindowsProxyCommandLine, quoteWindowsArg } from './check-deps.mjs';

test('quoteWindowsArg handles plain args, spaces, and trailing backslashes safely', () => {
  assert.equal(quoteWindowsArg('chrome'), 'chrome');
  assert.equal(quoteWindowsArg('C:\\Program Files\\nodejs\\node.exe'), '"C:\\Program Files\\nodejs\\node.exe"');
  assert.equal(quoteWindowsArg('C:\\Temp Dir\\'), '"C:\\Temp Dir\\\\"');
  assert.equal(quoteWindowsArg('a"b'), '"a\\"b"');
});

test('buildWindowsProxyCommandLine composes safe detached launch command', () => {
  const cmd = buildWindowsProxyCommandLine(
    'C:\\Program Files\\nodejs\\node.exe',
    'C:\\Users\\user\\agent-skills\\skills\\web-access\\scripts\\cdp-proxy.mjs',
    { browser: 'chrome', port: 3456, logFile: 'C:\\Users\\user\\AppData\\Local\\Temp\\cdp-proxy.log' },
  );
  assert.equal(
    cmd,
    '"C:\\Program Files\\nodejs\\node.exe" C:\\Users\\user\\agent-skills\\skills\\web-access\\scripts\\cdp-proxy.mjs --browser chrome --log-file C:\\Users\\user\\AppData\\Local\\Temp\\cdp-proxy.log',
  );
});

test('buildWindowsProxyCommandLine passes non-default port and rejects unsafe browser ids', () => {
  const cmd = buildWindowsProxyCommandLine('node.exe', 'cdp-proxy.mjs', { browser: 'edge', port: 4567 });
  assert.equal(cmd, 'node.exe cdp-proxy.mjs --browser edge --port 4567');
  assert.throws(
    () => buildWindowsProxyCommandLine('node.exe', 'cdp-proxy.mjs', { browser: 'chrome & calc' }),
    /非法浏览器标识/,
  );
});
