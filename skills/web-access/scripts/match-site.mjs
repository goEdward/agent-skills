#!/usr/bin/env node
// 根据用户输入匹配站点经验文件（跨平台，替代 match-site.sh）
// 用法：node match-site.mjs "用户输入文本"
// 输出：匹配到的站点经验内容，无匹配则静默

import fs from 'node:fs';
import { sitePatternFiles } from './user-data.mjs';

const query = (process.argv[2] || '').trim();

if (!query) {
  process.exit(0);
}

for (const [name, file] of sitePatternFiles()) {
  const domain = name.replace(/\.md$/, '');
  const raw = fs.readFileSync(file, 'utf8');

  // 提取 aliases
  const aliasesLine = raw.split(/\r?\n/).find((l) => l.startsWith('aliases:')) || '';
  const aliases = aliasesLine
    .replace(/^aliases:\s*/, '')
    .replace(/^\[/, '').replace(/\]$/, '')
    .split(',')
    .map((v) => v.trim().replace(/^["']|["']$/g, ''))
    .filter(Boolean);

  // 构建匹配模式
  const escaped = (t) => t.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const pattern = [`(?:^|[^a-z0-9.-])(?:[a-z0-9-]+\\.)*${escaped(domain)}(?=$|[^a-z0-9.-])`, ...aliases.map(escaped)].join('|');
  if (!new RegExp(pattern, 'i').test(query)) continue;

  // 跳过 frontmatter，输出正文
  const fences = [...raw.matchAll(/^---\s*$/gm)];
  const body = fences.length >= 2
    ? raw.slice(fences[1].index + fences[1][0].length).replace(/^\r?\n/, '')
    : raw;

  process.stdout.write(`--- 站点经验: ${domain} ---\n`);
  process.stdout.write(body.trimEnd() + '\n\n');
}
