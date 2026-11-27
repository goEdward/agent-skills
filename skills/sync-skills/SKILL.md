---
name: sync-skills
description: Audit, sanitize, and synchronize the multi-platform agent-skills GitHub repository (Antigravity / Codex / Claude Code on macOS & Windows), including checking global skill-management rules on first run. Trigger when the user asks to sync skills, push skill updates to GitHub, pull latest skills, check skill compliance, or initialize/verify skill rules and mounts.
---

# sync-skills

安全同步跨平台多 Agent 技能仓库到 GitHub 的标准工作流。

核心目标：**首次运行自动体检本机全局规则与挂载状态；推送前自动完成 Skill 规范校验与 Public 仓库防泄露安检，经用户明确确认后再提交推送。**

## 定位脚本与仓库（不依赖固定本机路径）

- 同步脚本位于本 Skill 目录下的 `scripts/sync_tool.py`（纯 Python 3 标准库，macOS / Windows 通用）。
- 仓库根目录 `<repo>` 自动动态解析（不硬编码固定路径）：
  1. 命令行 `--repo <path>`
  2. 本地私有配置 `~/.config/agent-secrets/sync-skills.env` 中的 `SKILLS_REPO_DIR`
  3. 从 `sync_tool.py` 所在位置向上自动定位 `.git` 根目录
- 所有自定义敏感词与本机路径配置只存放在 `~/.config/agent-secrets/sync-skills.env`（参见 `templates/config.example.env`），**绝不写入仓库内**。

## Step 0（首次与每次运行必检）：全局规则与环境自检

在执行任何同步前，先运行体检命令检查本机是否已安装仓库自带的全局规则（`rules/skills-management.md`）与挂载状态：

```bash
python3 <skill-dir>/scripts/sync_tool.py doctor
```
（Windows 下若无 `python3` 命令，使用 `python`。）

检查项包括：
1. **Antigravity 全局规则**：`~/.gemini/config/rules/skills-management.md` 是否已挂载；
2. **Codex 全局规则**：`~/.codex/AGENTS.md` 是否已包含受管的 `<!-- agent-skills:start --> ... <!-- agent-skills:end -->` 规则块；
3. **本机 Skill 挂载**：当前仓库是否已通过 `setup.py` 正确接入本机 Agent；
4. **本地脱敏配置提醒**：`~/.config/agent-secrets/sync-skills.env` 是否已配置自定义敏感词（未配置时仅提醒，不阻断）。

- **若检测到缺失全局规则或挂载未完成**：告知用户具体缺失项，并在征得同意后运行：
  ```bash
  python3 <repo>/setup.py
  ```
  自动补齐规则与挂载（不会覆盖用户原有的 `AGENTS.md` 或其他 Skill）。

## 场景 A：推送本地 Skill 修改到 GitHub（默认流程）

严格按以下 5 步执行，**不得跳过安检或未经确认直接 `git push`**：

### Step 1：检查仓库状态
```bash
python3 <skill-dir>/scripts/sync_tool.py status
```
- 若工作区无任何变更且无未推送提交，直接告知用户仓库已是最新状态并结束。
- 若发现新增、重命名或删除了 Skill，提示用户同步完成后需运行 `<repo>/setup.py` 刷新本机挂载。

### Step 2：运行自动化规范与隐私安检
```bash
python3 <skill-dir>/scripts/sync_tool.py audit
```
- 校验 `SKILL.md` frontmatter（`name` 匹配目录名、`description` 非空）、禁止提交的文件模式、高危密钥/本机绝对路径/个人邮箱/本地自定义敏感词及换行符。
- **若 `audit` 返回阻断错误（exit code != 0）**：立即停止，逐条列出命中文件和行号，协助用户脱敏或移入 `~/.config/agent-secrets/` 后重新运行 `audit`，直到完全通过。

### Step 3：AI 二次语义脱敏审查
阅读本次 `git diff`（及新增文件内容），对照 `references/security-checklist.md` 做二次语义复核：
- 是否混入真实店铺名、品牌内部词、客户资料、个人真实姓名、内部接口域名或私密业务 SOP？
- 通用 `SKILL.md` / 规则 / 脚本是否硬编码了特定本机路径或单一 Agent 私有工具名？

### Step 4：向用户汇报并请求推送确认
清晰列出以下摘要后，**等待用户明确同意**：
- 目标 GitHub 仓库与分支
- 将要提交的文件清单（新增 / 修改 / 删除）
- 建议的 Commit Message
- 全局规则自检 + 脱敏安检 + 语义复核结果

### Step 5：先拉取合并，再提交推送
获得用户确认后执行：

```bash
python3 <skill-dir>/scripts/sync_tool.py commit-push -m "<commit-message>"
```
- 若 `pull --rebase` 遇到冲突，严禁 `--force`，停止并展示冲突文件协助合并。
- 如涉及新增/删除 Skill、规则变更或 `agents/<agent>/SKILL.md` 变更，运行 `python3 <repo>/setup.py` 刷新本机挂载。

## 场景 B：从 GitHub 拉取最新 Skill 到本机

```bash
python3 <skill-dir>/scripts/sync_tool.py pull --refresh-setup
```
自动完成 `git pull --rebase --autostash` 并调用 `<repo>/setup.py` 同步本机技能挂载与全局规则。

## 场景 C：全仓库体检

```bash
python3 <skill-dir>/scripts/sync_tool.py audit --all
```
