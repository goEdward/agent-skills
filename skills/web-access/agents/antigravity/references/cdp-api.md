# 本地 CDP 操作参考

仅当主 skill 选中本地外部浏览器路径时加载；公开静态读取不运行本节检查。

## 运行时与连接

1. `<skill-dir>` 是本次加载的 `SKILL.md` 所在目录（以当前加载的 `SKILL.md` 所在目录为准，不依赖固定安装路径）；Node.js 必须 22+，先用 PATH 中的 `node`（如不在默认 PATH 中，可用本机实际 Node 22+ 路径，如 `/opt/homebrew/bin/node` 或 nvm 路径）。
2. 本任务首次进入本地浏览器路径时，说明将使用实际选定浏览器的登录态，只操作任务新建的后台页；自动化可能触发站点检测。目标含糊、意外跳转或身份不符时再询问，已有授权不重复确认。
3. 执行以下快速检查。它只验证实时 CDP，不创建探测页；Profile 验证延迟至首次创建任务 tab。

```bash
"<node-bin>" "<skill-dir>/scripts/check-deps.mjs" --quick
# 临时选浏览器，不改长期偏好：
"<node-bin>" "<skill-dir>/scripts/check-deps.mjs" --quick --browser chrome
```

`exit 0` 继续；`exit 2` 需要选择浏览器；`exit 1` 按明确错误处理，不在同一路径反复申请授权。连接过程单次最多 8 秒，外围进程启动和健康检查另计。授权被拒绝或断线后冷却 30 秒，状态查询不触发连接；用户明确同意重新授权时，待冷却结束再试。

配置优先使用 `~/.web-access/config.env`，旧 skill 根目录配置仅在新文件尚不存在时兼容读取。首次检查会复制旧数据，保留原件、不覆盖新位置。现有 Chrome / Chrome Canary / Chromium / Edge 选择规则及 Windows 自动选择规则保持兼容，不要求改浏览器。

## Profile 与后台页面

- `WEB_ACCESS_BROWSER_PROFILE` 指定内部目录，兼容 `WEB_ACCESS_CHROME_PROFILE`；允许 `Profile8` 的空格差异，不把 `Profile 80` 当成 `Profile 8`。
- 正向 `WEB_ACCESS_CHROME_ANCHOR_URLS` / 排除 `WEB_ACCESS_CHROME_EXCLUDE_URLS` 仅对候选上下文排序。指定 Profile 后读取临时版本页的实际 `Profile Path` 最终验证，失败不降级到其它 Profile。锚点可独立使用；多个上下文无法唯一确定时报 `profile_ambiguous`。
- 配置在创建任务页时重新读取，无需为修改锚点重启服务。上下文失效时自动清除缓存、重定位并重试一次。
- 所有既存 tab（包括空白页）都视作用户页面，不复用、不导航、不关闭。每个任务用随机 UUID 的 `OWNER`，所有 target 操作必须携带该 owner。
- Chrome 151+ 优先尝试后台创建并验证归属。仅用户明确启用 `WEB_ACCESS_ALLOW_FOREGROUND_FALLBACK=true` 时，Proxy 才允许一次 `window.open` 兼容桥接；可能短暂抢焦点。桥接页转为 `about:blank`，由内部 owner 保留；配置变化后解除保留，进入闲置清理。

## 带认证的客户端

只使用 `cdp-call.mjs`，不手工读取/复制令牌，不直接请求裸 HTTP。服务仅监听本机，拒绝浏览器来源、异常 Host、错误 HTTP 方法和非本任务 target。

```bash
OWNER="$("<node-bin>" -e 'process.stdout.write("web-access-" + crypto.randomUUID())')"
# 完整原始 URL 直接传入；客户端自动编码查询参数，不要二次编码。
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" new --owner "$OWNER" --url 'https://example.com/path?a=1&b=2'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" info --owner "$OWNER" --target ID
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" eval --owner "$OWNER" --target ID --expr 'document.title'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" navigate --owner "$OWNER" --target ID --url 'https://example.com/next'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" back --owner "$OWNER" --target ID
```

`new` / `navigate` / `back` 返回 `loaded`、`waitStatus`、脱敏 `url` 和 `elapsedMs`。拿到 targetId 不代表加载成功；即使 `loaded=true`，仍按主 skill 检查目标内容是否出现。`loaded=false` 时用 `info` / `eval` 看实际状态，新页仍需清理。

优先使用页面实际生成的链接和控件。原始 token / 签名 URL 仅作为确有需要的本地导航输入，不输出到对话、搜索、第三方服务或经验文件。列表、导航和 frame URL 在输出边界脱敏；自由 `eval` 的任意字符串无法自动可靠辨认，表达式也必须避免返回凭据、存储或敏感原始链接。

## DOM、点击、上传与滚动

```bash
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" click --owner "$OWNER" --target ID --selector 'button.expand'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" click-at --owner "$OWNER" --target ID --selector 'button.upload'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" scroll --owner "$OWNER" --target ID --direction bottom
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" set-files --owner "$OWNER" --target ID --json '{"selector":"input[type=file]","files":["/absolute/path/image.png"]}'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" screenshot --owner "$OWNER" --target ID --output /tmp/web-access-shot.png
```

`click` 是 DOM `el.click()`，`scroll` 是 `window.scroll*()`；默认使用，不能描述为真人 GUI。页面只响应指针事件时使用 `click-at`（CDP 鼠标输入），仍属自动化，不承诺绕过检测。上传只使用用户任务指定的文件。eval 支持 `--stdin` 输入长表达式，请求体最大 1 MB。

开放 Shadow DOM 和同源 iframe 可递归读取；跨域 iframe 使用下节能力。closed Shadow DOM 不能靠普通页面 eval 穿透，改用页面交互或截图。虚拟/无限列表分段滚动并确认内容增长，避免只读到首屏。

## 跨站 iframe

```bash
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" frames --owner "$OWNER" --target ID
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" eval --owner "$OWNER" --target ID --frame FRAME_ID --expr 'document.body.innerText'
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" click-xy --owner "$OWNER" --target ID --x 120 --y 90
```

`frames` 列出主文档直接 iframe 的 `index`（零起）、`frameId`、`crossProcess`、脱敏 `url`、可选 `id/name/src`；不列嵌套或 Shadow DOM 中的 iframe。未加载出文档时 frameId 为 null。先识别目标，再使用返回的 frameId；不能使用其它页面或猜测的 targetId。

`eval --frame` 在 iframe 隔离环境中执行：共享 DOM，看不到页面自己的 JS 全局变量。坐标点击按主页面视口坐标进入嵌套 frame，最多 5 层，按边框、缩放和滚动换算；不会激活标签页。`clicked=true` 仅表示事件已发出，仍需核对结果。跨站内容的访问仍受主任务范围和写操作授权约束。

## 媒体

图片承载信息时，先读取任务相关的媒体 URL，再定向分析；懒加载资源先滚动。公开媒体可按环境能力读取，需登录的资源留在浏览器内分析，不导出会话。视频可用 `<video>` 的时长和 seek 加截图采样；采样不能当成已经完整看过视频，应说明覆盖范围。

## 结束与恢复

任务最外层使用 `try/finally`。在 `finally` 依次执行：

```bash
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" close-owned --owner "$OWNER"
"<node-bin>" "<skill-dir>/scripts/cdp-call.mjs" targets --owner "$OWNER"
```

核验只覆盖当前任务 owner；内部 Profile 桥接/探测页另管。只有任务列表为空且 `retained=0` 才报告已关闭。关闭失败或断线时保留归属记录，向用户说明数量，重连后继续回收；闲置 5 分钟自动回收只作异常兜底，不能替代正常清理。中途改回静态路径也要执行上述清理。

单页用完可提前 `close --owner ... --target ...`。仅关闭全部受管页会导致整个浏览器没有页面时，Proxy 会先创建一个不受任务管理的空白页，避免浏览器退出。不要为了清理任务而停止共享服务。

## 服务维护与错误

```bash
"<node-bin>" "<skill-dir>/scripts/proxy-maintenance.mjs" status
"<node-bin>" "<skill-dir>/scripts/proxy-maintenance.mjs" restart
```

`status` 不连接浏览器、不输出令牌。健康状态包括实例 ID、运行代码版本、连接状态、请求和命令占用。维护以实例身份核对，服务仍有任务、请求、命令或连接进行中时拒绝；不批量查杀进程。旧认证实例的单次迁移见 README；切换浏览器先 `stop`，再检查目标浏览器。普通任务不自动重启共享服务。

| 状态 | 含义与处理 |
|---|---|
| 401 | 运行时认证失效，重新检查连接；不手工复制令牌 |
| 403 | 来源、Host 或 owner/target 不符；核对任务归属，不绕过 |
| 405 / 413 | 方法或请求体错误；使用客户端，缩小表达式 |
| 409 / busy | 实例变化或服务有任务；保留服务，稍后维护 |
| profile_ambiguous / Profile 未加载 | 核对实际配置和窗口，不切到其它 Profile |
| loaded=false | 检查实际页面和目标内容，仍须清理 |
| 503 | 正在维护；等待维护结束 |
