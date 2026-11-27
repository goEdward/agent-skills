# 本地历史与书签

仅在用户明确查本人访问记录、书签或需要从中定位内部系统时使用，不为普通搜索扫描本地资料。
定位 Node.js 22+（PATH 优先；Desktop 中可用 workspace dependencies loader 返回的 Node 路径）。本路径无需启动任何浏览器、Proxy 或依赖检查。

```bash
"<node-bin>" "<skill-dir>/scripts/find-url.mjs" [关键词...] [--only bookmarks|history] [--browser chrome|chrome-canary|chromium|edge] [--limit N] [--since 1d|7h|YYYY-MM-DD] [--sort recent|visits] [--raw]
```

关键词按空格分词、多词 AND，匹配 title 和 url；默认遍历已安装的 Chromium 系浏览器（Chrome / Chrome Canary / Chromium / Edge）。`--since`、`--sort` 只作用于历史，默认按最近访问倒序。书签无时间维度，按目标给关键词。

结果默认脱敏。只有用户明确要求访问该本地链接时才用 `--raw`，将原始值立即用于本地浏览器导航，不复制到对话、第三方服务或经验文件。敏感历史链接应通过本地流程直接传递，避免不必要的原始输出；不得因为链接来自本人历史就推断用户授权外部写操作。

历史读取使用临时 SQLite 主库和 WAL 快照，临时文件只允许当前用户访问并在结束时删除。不要自行读取 cookies、Login Data、会话文件或 Profile 中其它数据。
