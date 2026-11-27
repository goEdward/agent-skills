import test from 'node:test';
import assert from 'node:assert/strict';
import { classifyUrlForJina, hostnameIsPrivate, redactUrl } from './url-safety.mjs';

test('allows ordinary public URLs', () => {
  assert.equal(classifyUrlForJina('https://example.com/article?a=1').ok, true);
});

test('rejects credentials, sensitive query, private hosts, and sensitive paths', () => {
  for (const url of [
    'https://user:pass@example.com/a',
    'https://example.com/a?x-amz-signature=abc',
    'http://127.0.0.1:8080/a',
    'https://example.com/session/abc',
    'https://example.com/%73ession/abc',
    'https://example.com/login?next=%2Fhome',
    'https://example.com/oauth/authorize?client_id=demo',
    'https://example.com/sso/callback',
  ]) assert.equal(classifyUrlForJina(url).ok, false, url);
});

test('recognizes local host forms', () => {
  assert.equal(hostnameIsPrivate('localhost'), true);
  assert.equal(hostnameIsPrivate('192.168.1.2'), true);
  assert.equal(hostnameIsPrivate('[fe90::1]'), true); // fe80::/10 link-local
  assert.equal(hostnameIsPrivate('[::]'), true); // unspecified
  assert.equal(hostnameIsPrivate('[::1]'), true); // loopback
  assert.equal(hostnameIsPrivate('[::ffff:192.168.1.2]'), true); // IPv4-mapped private
  assert.equal(hostnameIsPrivate('[::ffff:8.8.8.8]'), false);
  assert.equal(hostnameIsPrivate('example.com'), false);
});

test('redacts sensitive query values while preserving ordinary parameters', () => {
  assert.equal(
    redactUrl('https://example.com/a?token=abc&lang=zh'),
    'https://example.com/a?token=%5BREDACTED%5D&lang=zh',
  );
});

test('rejects sensitive fragment values before sending a URL to Jina', () => {
  for (const url of [
    'https://example.com/callback#access_token=abc',
    'https://example.com/callback#token=abc&token_type=Bearer',
    'https://example.com/callback#access_token%3Dabc',
  ]) assert.equal(classifyUrlForJina(url).ok, false, url);
  assert.equal(classifyUrlForJina('https://example.com/article#lang=zh').ok, true);
});

test('redacts sensitive fragment values while preserving ordinary fragments', () => {
  assert.equal(
    redactUrl('https://example.com/article#access_token=abc&lang=zh'),
    'https://example.com/article#access_token=%5BREDACTED%5D&lang=zh',
  );
  assert.equal(
    redactUrl('https://example.com/article#/done?access_token=abc&lang=zh'),
    'https://example.com/article#/done?access_token=%5BREDACTED%5D&lang=zh',
  );
  assert.equal(
    redactUrl('https://example.com/article#access_token%3Dabc'),
    'https://example.com/article#%5BREDACTED%5D',
  );
  assert.equal(redactUrl('https://example.com/article#lang=zh'), 'https://example.com/article#lang=zh');
});

test('sensitive names use boundaries and camelCase without zipcode false positives', () => {
  assert.equal(classifyUrlForJina('https://example.com/a?zipcode=10001&keyboard=us').ok, true);
  for (const name of ['email', 'apiKey', 'accessToken', 'xsec_token', 'X-Amz-Signature', 'sessionId']) {
    const url = `https://example.com/a?${name}=private-value`;
    assert.equal(classifyUrlForJina(url).ok, false, name);
    assert.ok(!redactUrl(url).includes('private-value'), name);
  }
});

test('sensitive path values and personal data are removed from URL output', () => {
  for (const url of [
    'https://example.com/reset-password/demo-value',
    'https://example.com/share/demo-value',
    'https://example.com/%73ession/demo-value',
    'https://example.com/users/demo%40example.com',
    'https://example.com/a?q=demo%40example.com',
    '/oauth/callback?token=demo-value',
  ]) {
    assert.ok(!redactUrl(url).includes('demo-value'), url);
    assert.ok(!redactUrl(url).includes('demo%40example.com'), url);
    if (url.startsWith('https:')) assert.equal(classifyUrlForJina(url).ok, false, url);
  }
});

test('compact credential names, repeated values and fragment personal data remain protected', () => {
  for (const name of ['apikey', 'APIKEY', 'accesstoken', 'authkey', 'credentials', 'sid']) {
    const url = `https://example.com/a?${name}=private-value`;
    assert.equal(classifyUrlForJina(url).ok, false, name);
    assert.ok(!redactUrl(url).includes('private-value'), name);
  }
  const repeated = 'https://example.com/a?q=public&q=demo%40example.com';
  assert.equal(classifyUrlForJina(repeated).ok, false);
  assert.equal(new URL(redactUrl(repeated)).searchParams.getAll('q').length, 2);
  assert.ok(!redactUrl(repeated).includes('demo%40example.com'));
  for (const fragment of ['q=demo%40example.com', '/done?email=demo%40example.com', 'demo%40example.com']) {
    const url = 'https://example.com/a#' + fragment;
    assert.equal(classifyUrlForJina(url).ok, false);
    assert.ok(!redactUrl(url).includes('demo%40example.com'));
  }
});
