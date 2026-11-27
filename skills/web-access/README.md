# web-access（跨平台 · Antigravity & Codex 适配版 2.9.1）

> **源仓库**：https://github.com/goEdward/agent-skills（路径 `skills/web-access`）  
> **AI 更新方式**：选中本 Skill 对 AI 说“按源仓库更新 `web-access`，严禁覆盖本地隐私配置”即可。

联网研究、来源选择、网页读取与外部浏览器 CDP 任务的统一入口（基于上游 `eze-is/web-access v2.6.0` 选择性合并与本地增强）。

## 双端结构说明

本 Skill 共享全部底层 Node.js 脚本（`scripts/*.mjs`）与配置模板，并通过 `agents/` 差异目录分别适配不同 Agent 的工具路由：

| 目录 | 作用 |
| :--- | :--- |
| `scripts/` | 共享底层 CDP、Profile 选择、SQLite 快照、网址脱敏与站点匹配脚本（需 Node.js 22+） |
| `templates/config.env.template` | 本地外部浏览器配置模板 |
| `agents/antigravity/` | **Antigravity 专属层**：优先 `search_web` + `browsermcp` → 本地外部 CDP（默认不自动调用内置 `browser` subagent），含自动挂载的 `rules/web-access-routing.md` |
| `agents/codex/` | **Codex 专属层**：优先 Codex 原生搜索与内置浏览器 → 回退本地外部 CDP，含 `openai.yaml` |
| `SKILL.md` | 通用基线入口（供其他兼容 Agent 使用） |

## 🔒 本地配置（物理隔离，绝不进 Git）

每台设备的浏览器类型、Profile 名称与 CDP 端口不同，配置统一保存在仓库外部：

- **macOS / Linux**：`~/.web-access/config.env`
- **Windows**：`%USERPROFILE%\.web-access\config.env`

新设备部署时，可运行仓库根目录的 `python3 setup.py --only web-access --init-secrets`（仅在 `~/.web-access/config.env` **不存在**时由 `templates/config.env.template` 创建），再按本机浏览器实际路径填写。个人私密站点经验可放 `~/.web-access/site-patterns/`（同名优先于仓库内置经验）。

> **⚠️ 更新安全声明**：`git pull` 与 `setup.py`（含 `--init-secrets`）**绝不会覆盖已存在的 `~/.web-access/config.env` 或个人 `site-patterns/`**。AI 协助更新时也严禁以模板覆盖这些本地文件。

## 依赖与测试

- Node.js 22+（仅使用标准库，零 `npm install` 依赖）
- **Windows 防重复弹出 CDP 授权**：`check-deps.mjs` 在 Windows 下优先通过 WMI (`Win32_Process.Create`) 脱离命令执行器 Job Object 启动常驻 Proxy，并将首次授权等待超时放宽至 25 秒。若本地沙箱限制 WMI 导致仍重复弹窗，在普通 PowerShell 运行一次 `platforms/win/start-cdp-proxy.ps1` 常驻即可（停止用 `platforms/win/stop-cdp-proxy.ps1`）。
- 运行单元测试：
  ```bash
  node --test scripts/*.test.mjs
  ```
