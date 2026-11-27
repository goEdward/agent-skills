import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  extractProfilePath,
  makeProfileSelectionConfig,
  profilePathMatches,
  profileSelectionKey,
  rankProfileContexts,
} from './profile-selection.mjs';

test('Profile Path 支持中英文页面标签', () => {
  assert.equal(
    extractProfilePath('Profile Path\t/Users/test/Chrome/Profile 8\n'),
    '/Users/test/Chrome/Profile 8'
  );
  assert.equal(
    extractProfilePath('个人资料路径\t/Users/test/Chrome/Profile 8\n'),
    '/Users/test/Chrome/Profile 8'
  );
});

test('Profile 目录匹配允许空格差异但不误匹配相邻编号', () => {
  assert.equal(profilePathMatches('/Users/test/Profile 8', 'Profile8'), true);
  assert.equal(profilePathMatches('/Users/test/Profile 8', 'Profile 8'), true);
  assert.equal(profilePathMatches('/Users/test/Profile 80', 'Profile8'), false);
});

test('配置包含正向与排除标签页锚点，并可感知配置变化', () => {
  const values = {
    WEB_ACCESS_BROWSER_PROFILE: 'Profile 8',
    WEB_ACCESS_CHROME_ANCHOR_URL: '',
    WEB_ACCESS_CHROME_ANCHOR_URLS: 'x.com, v2ex.com',
    WEB_ACCESS_CHROME_EXCLUDE_URLS: 'amazon.com, sellersprite.com',
  };
  const config = makeProfileSelectionConfig(key => values[key] || null);
  assert.deepEqual(config.anchorUrls, ['x.com', 'v2ex.com']);
  assert.deepEqual(config.excludeUrls, ['amazon.com', 'sellersprite.com']);
  assert.equal(profileSelectionKey(config), profileSelectionKey({ ...config }));
  values.WEB_ACCESS_BROWSER_PROFILE = 'Profile 6';
  assert.notEqual(profileSelectionKey(config), profileSelectionKey(makeProfileSelectionConfig(key => values[key] || null)));
});

test('按正向锚点、排除锚点和页面数排序候选上下文', () => {
  const stats = new Map([
    ['work', { positive: 0, negative: 2, pages: 5 }],
    ['target', { positive: 1, negative: 0, pages: 1 }],
    ['other', { positive: 0, negative: 0, pages: 2 }],
  ]);
  assert.deepEqual(rankProfileContexts(['work', 'other', 'target'], stats), ['target', 'other', 'work']);
});
