---
name: sync-skills
description: Audit, sanitize, test-gate, and synchronize the multi-platform agent-skills GitHub repository (Antigravity / Codex / Claude Code on macOS & Windows), including checking global skill-management rules on first run. Trigger when the user asks to sync skills, push skill updates to GitHub, pull latest skills, check skill compliance, or initialize/verify skill rules and mounts.
metadata:
  repository: "https://github.com/goEdward/agent-skills"
  source_path: "skills/sync-skills"
---

# sync-skills

安全同步跨平台多 Agent 技能仓库到 GitHub 的标准工作流。

核心目标：**首次运行自动体检全局规则与运行环境；推送前自动完成规范校验、隐私安检（含真实 ASIN 与本地敏感词拦截）与单元测试门禁，经用户明确确认后再提交推送；拉取更新严禁覆盖任何本地隐私文件。**

## 定位脚本与仓库（不依赖固定本机路径）

- 同步脚本位于本 Skill 目录下的 `scripts/sync_tool.py`（纯 Python 3 标准库，macOS / Windows 通用）。
- 仓库根目录 `<repo>` 自动动态解析（命令行 `--repo` > `~/.config/agent-secrets/sync-skills.env` 的 `SKILLS_REPO_DIR` > 从脚本位置向上查找 `.git`）。
- 所有自定义敏感词与本机路径配置只存放在 `~/.config/agent-secrets/sync-skills.env`（参见 `templates/config.example.env`），**绝不写入仓库内**。

## Step 0（首次与每次运行必检）：全局规则与环境自检

```bash
python3 <skill-dir>/scripts/sync_tool.py doctor
```
（Windows 下若无 `python3` 命令，使用 `py -3` 或 `python`。）

`doctor` 会联动 `<repo>/setup.py --verify --json`，一次性检查：
1. **Antigravity 全局规则**：`~/.gemini/config/rules/skills-management.md` 是否已挂载；
2. **Codex 全局规则**：`~/.codex/AGENTS.md` 是否已包含受管的 `<!-- agent-skills:start --> ... <!-- agent-skills:end -->` 规则块；
3. **本机挂载与运行时依赖**：各 Skill 挂载是否有效、Python/Node/`openpyxl` 与本地隐私文件状态；
4. **本地脱敏配置提醒**：`~/.config/agent-secrets/sync-skills.env` 是否已配置自定义敏感词。

若检测到缺失全局规则或挂载未完成，告知用户并经同意后运行 `python3 <repo>/setup.py` 自动补齐。

## 场景 A：推送本地 Skill 修改到 GitHub（默认流程）

严格按以下 5 步执行，**不得跳过安检或未经确认直接 `git push`**：

### Step 1：检查仓库状态
```bash
python3 <skill-dir>/scripts/sync_tool.py status
```
- 若工作区无任何变更且无未推送提交，直接告知用户仓库已是最新状态并结束。

### Step 2：运行自动化规范、隐私安检与测试门禁
```bash
python3 <skill-dir>/scripts/sync_tool.py audit --with-tests
```
检查项包括：
- 所有 `SKILL.md` frontmatter（`name` 匹配目录名、`description` 非空）及 `skill.manifest.json` 完整性（含 `repository` URL、模板存在性、`localSecretsTarget` 必须位于仓库外）；
- 禁止提交的文件模式（`.env`、私钥、本地临时文件等）；
- 高危密钥/Token、本机绝对路径、个人邮箱、非占位真实 Amazon ASIN、本地自定义敏感词及换行符；
- 若本次修改触及任意 Skill 的 `scripts/` 或 `tests/`，自动运行该 Skill 的单元测试（`selftest.py` / `unittest` / `node --test`）。

**若任一项阻断失败（exit code != 0）**：立即停止，列出具体文件与行号协助修复并重跑，直到全部通过。

### Step 3：AI 二次语义脱敏审查
阅读本次 `git diff`（及新增文件内容），对照 `references/security-checklist.md` 做二次语义复核（检查真实店铺名、品牌内部词、客户资料、个人姓名、硬编码工具名/本机路径）。

### Step 4：向用户汇报并请求推送确认
清晰列出：目标 GitHub 仓库与分支、变更文件清单、建议的 Commit Message、以及规则体检 + 隐私安检 + 测试门禁结果，**等待用户明确同意**。

### Step 5：先拉取合并，再提交推送
获得确认后执行：

```bash
python3 <skill-dir>/scripts/sync_tool.py commit-push -m "<commit-message>"
```
- 脚本内置安检 + 测试门禁 + `pull --rebase` + `push`，遇 TLS/HTTP2 抖动会自动回退 `HTTP/1.1` 重试；
- 若 `pull --rebase` 遇到冲突，严禁 `--force`，停止并协助合并；
- 如涉及新增/删除 Skill、规则变更或 `agents/<agent>/SKILL.md` 变更，运行 `python3 <repo>/setup.py` 刷新本机挂载。

## 场景 B：从 GitHub 拉取最新 Skill 到本机（严禁覆盖本地隐私文件）

```bash
python3 <skill-dir>/scripts/sync_tool.py pull --refresh-setup
```
- 安全执行 `git pull --rebase --autostash`（遇 TLS/HTTP2 抖动自动回退 HTTP/1.1）并刷新本机挂载；
- **绝不覆盖** `~/.config/agent-secrets/**` 和 `~/.web-access/**` 中任何已存在的本地隐私文件；
- 若本次拉取更新了任意 Skill 的 `templates/`，脚本会自动列出变更模板提醒用户对照补充新字段。

## 场景 C：全仓库体检与全量测试

```bash
python3 <skill-dir>/scripts/sync_tool.py audit --all --with-tests
```
