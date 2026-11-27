# AGENTS.md — 仓库开发与维护契约

本仓库为**跨平台（macOS / Windows）、多 Agent（Antigravity / Codex / Claude Code）**共用的公开 Skill 仓库。任何 AI Agent 在本仓库中新增、修改或同步 Skill 时，必须遵守以下规则：

## 1. GitHub 仓库规范目录（严禁写死固定本地克隆路径）

- 仓库根目录结构：
  - `rules/skills-management.md`：跨 Agent 全局规则模板（由 `setup.py` 自动安装到各端）
  - `skills/<skill-name>/`：每个 Skill 一个独立大目录（小写 kebab-case）
  - `setup.py`：跨平台一键挂载器，通过自身位置动态定位 `<repo>`，不依赖固定本机目录
- 每个 Skill 目录默认包含以下子结构（不使用的子目录放 `.gitkeep` 占位）：
  - `SKILL.md`：通用版主入口（所有 Agent 默认加载）
  - `README.md`：人类可读文档（说明用途、多平台用法、本地隐私配置）
  - `skill.manifest.json`：元信息清单
  - `scripts/`：跨平台脚本（优先 Python 3 标准库或 Node.js，禁止写死单系统路径）
  - `references/`：参考资料
  - `templates/`：配置模板（如 `config.example.env`）
  - `agents/{antigravity,codex,claude}/`：仅当某个 Agent 必须有差异化指令时，才在对应子目录中放置专属 `SKILL.md`
  - `platforms/{mac,win}/`：仅当需要系统专属辅助脚本或说明时使用

## 2. 跨 Agent / 跨系统编写规范

- **Frontmatter 必填**：每个 `SKILL.md` 顶部必须包含 YAML frontmatter，且 `name` 必须与 `<skill-name>` 目录名完全一致，`description` 不能为空。
- **工具无关表述**：通用 `SKILL.md` 正文只描述目标与命令（例如 `python3 scripts/foo.py`），不硬编码特定 Agent 的私有工具名。
- **路径无关**：脚本中必须通过 `Path(__file__).resolve()` 定位仓库，通过 `pathlib.Path.home()` 定位用户目录，严禁硬编码 `/Users/<username>/`、`C:\Users\<username>\` 或假定仓库一定在某一固定目录。
- **行尾符**：保持 LF 换行符（`.ps1` / `.bat` / `.cmd` 除外）。

## 3. Public 仓库安全底线

- **绝不提交任何隐私信息**：包括 API Key、Token、Cookie、个人邮箱、本机绝对路径、客户/店铺/公司内部信息。
- **隐私物理隔离**：本地隐私配置统一放在 `~/.config/agent-secrets/<skill-name>.env`，仓库内只放 `templates/config.example.env`。
- **推送前必须安检**：任何推送到 GitHub 的操作，必须先运行 `sync_tool.py doctor` 和 `sync_tool.py audit`，向用户展示变更清单与确认结果后才能执行 `git push`。

## 4. 新增或删除 Skill 后的挂载刷新

- 新增、重命名或删除 Skill，或更新 `rules/skills-management.md` 后，运行 `python3 <repo>/setup.py` 刷新本机 Antigravity / Codex 挂载与全局规则。
