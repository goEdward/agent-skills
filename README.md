# Agent Skills（跨平台 · 多 Agent 个人技能库）

一套同时适配 **macOS / Windows** 与 **Antigravity（反重力）/ Codex / Claude Code** 的个人 Skill 同步仓库。

核心设计：**一个 Skill 一个独立大目录**，所有路径在 GitHub 仓库内均按相对结构规范组织（每台设备可 clone 到任意本地目录，不写死固定路径）；**公开仓库只存通用代码、规则与模板，隐私配置物理隔离在仓库外部的本地目录**。

---

## GitHub 仓库规范目录结构

```text
<repo>/                        # GitHub 仓库根目录（任意本地克隆路径均可）
├── README.md                  # 仓库总说明（本文件）
├── AGENTS.md                  # 仓库内开发与脱敏契约
├── setup.py                   # 跨平台一键挂载器（自动识别当前 <repo> 路径，挂载 Skills + 全局 Rule）
├── .gitignore                 # 防误提交兜底规则
├── .gitattributes             # 强制 LF 换行符，避免 Win/Mac 冲突
├── rules/                     # 跨 Agent 全局规则模板（新建/修改 Skill 时自动规范目录与隐私拆分）
│   └── skills-management.md
└── skills/                    # 所有 Skill 集合（每个 Skill 一个独立大目录）
    └── <skill-name>/
        ├── SKILL.md           # 【主力】通用 Skill 定义（所有 Agent 默认读取）
        ├── README.md          # 该 Skill 的独立说明（多平台用法 & 本地隐私配置说明）
        ├── skill.manifest.json# 元数据：声明支持的 Agent、OS 与配置模板
        ├── scripts/           # 通用跨平台脚本（优先 Python / Node.js）
        ├── references/        # 通用参考文档
        ├── templates/         # 配置模板（如 config.example.env，绝不含真实密钥）
        ├── agents/            # 按 Agent 差异化覆盖层（无差异则留空）
        │   ├── antigravity/   # 仅需 Antigravity 专属版本时，在此放 SKILL.md
        │   ├── codex/         # 仅需 Codex 专属版本时，在此放 SKILL.md
        │   └── claude/        # 仅需 Claude Code 专属版本时，在此放 SKILL.md
        └── platforms/         # 按操作系统差异化辅助脚本/说明（无差异则留空）
            ├── mac/           # macOS 专属脚本或说明
            └── win/           # Windows 专属脚本或说明
```

### 各平台 / Agent 如何接入？

| 目标 Agent | 本机接入位置 | `setup.py` 自动完成的工作 |
| :--- | :--- | :--- |
| **Antigravity（反重力）** | `~/.gemini/config/skills.json`<br>`~/.gemini/config/rules/skills-management.md` | 注册 `<repo>/skills`（若有 `agents/antigravity/SKILL.md` 则自动启用 `.local-views/antigravity/` 覆盖视图）；并挂载全局规则 `skills-management.md` |
| **Codex** | `~/.codex/skills/<skill-name>`<br>`~/.codex/AGENTS.md` | macOS 用符号链接、Windows 用目录联接（`mklink /J`，免管理员权限）挂载每个 Skill；并在 `~/.codex/AGENTS.md` 安全追加受管规则块（保留原有内容） |
| **Claude Code（可选）** | `~/.claude/skills/<skill-name>` | 检测到 `~/.claude` 或显式传 `--agent claude` 时自动挂载 |

---

## 新设备快速上手

仓库可以 clone 到当前设备的**任意路径**（下文以 `<your-local-dir>` 表示，例如 `~/agent-skills` 或 `D:\dev\agent-skills`）：

**macOS / Linux：**
```bash
git clone https://github.com/goEdward/agent-skills.git <your-local-dir>
python3 <your-local-dir>/setup.py
```

**Windows (PowerShell)：**
```powershell
git clone https://github.com/goEdward/agent-skills.git <your-local-dir>
python <your-local-dir>\setup.py
```

查看当前挂载与全局规则状态：
```bash
python3 <your-local-dir>/setup.py --status
```

### 本地隐私配置（物理隔离，绝不进 Git）

所有真实密钥、API Token、本机路径、私人自定义敏感词，统一存放在**仓库外部**的固定本地目录：

- **macOS / Linux**：`~/.config/agent-secrets/<skill-name>.env`
- **Windows**：`%USERPROFILE%\.config\agent-secrets\<skill-name>.env`

每个 Skill 如需本地配置，会在自己的 `templates/config.example.env` 中给出字段模板，复制过去填写即可。

---

## 日常使用与同步闭环

1. **新建或修改 Skill**：受益于全局规则 `skills-management.md`，你在任意工作区让 AI 写 Skill 时，它会自动放入 `<repo>/skills/<name>/`、自动把隐私拆离到 `~/.config/agent-secrets/`，并提示你是否同步。
2. **推送到 GitHub**：调用 `sync-skills`（或在对话中说“同步 skills”）。首次运行会自动自检本机全局规则是否就位，随后完成规范校验、Public 仓库脱敏扫描、拉取合并，经你确认后推送。
3. **换设备拉取更新**：运行 `<repo>/skills/sync-skills/platforms/mac/pull-skills.command`（macOS）或 `win/pull-skills.ps1`（Windows），脚本会自动定位所在仓库并完成 `pull + setup.py`。

---

## 已收录 Skill

| Skill | 作用 | 支持 Agent | 支持系统 |
| :--- | :--- | :--- | :--- |
| [`sync-skills`](./skills/sync-skills/README.md) | 自检全局规则、校验 Skill 规范、扫描隐私防泄露并安全同步至 GitHub | Antigravity / Codex / Claude | macOS / Windows |
