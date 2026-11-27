# Agent Skills（跨平台 · 多 Agent 个人技能库）

一套同时适配 **macOS / Windows** 与 **Antigravity（反重力）/ Codex / Claude Code** 的 Skill 集合。

核心设计：**一个 Skill 一个独立大目录**，所有路径在 GitHub 仓库内均按相对结构规范组织（每台设备可 clone 到任意本地目录）；**公开仓库只存通用代码、规则与模板，隐私配置物理隔离在仓库外部，更新永不覆盖本地文件**。

---

## 🤖 直接让 AI 帮你一键安装 / 更新（推荐普通使用者）

你不需要懂 Git 或手动修改配置文件，直接把下面任意一句发给你的 **Antigravity / Codex / Claude Code**：

- **首次安装全部业务 Skill**：
  > 请按 `https://github.com/goEdward/agent-skills` 仓库 README 中的「给 AI 的安装协议」帮我以使用者模式（`--user`）安装所有业务 skill，不要覆盖我本地已有配置文件，并做安装后校验。
- **只安装其中某几个 Skill**：
  > 请按 `https://github.com/goEdward/agent-skills` 仓库 README 中的「给 AI 的安装协议」只帮我安装 `listing-generation` 和 `amazon-st-search-terms`（`--user --only`），并做安装后校验。
- **后续一键更新**：
  > 请帮我更新本地已安装的 `agent-skills` 到 GitHub 最新版并刷新挂载，严禁覆盖我的本地隐私配置文件。

### 给 AI 的标准安装 / 更新协议（AI Agents: Follow Strictly）

当你受用户委托安装或更新本仓库时，**严格按以下 4 步执行**，不要手写软链接或手动改写 `skills.json`：

1. **获取或更新仓库到本地目录**（如 `~/agent-skills` 或用户指定路径 `<dir>`）：
   - 已有 Git：首次执行 `git clone https://github.com/goEdward/agent-skills.git "<dir>"`；更新执行 `git -C "<dir>" pull --ff-only`。
   - 无 Git 环境兜底：下载仓库 ZIP（`https://github.com/goEdward/agent-skills/archive/refs/heads/main.zip`）解压至 `<dir>`。
2. **始终通过 `setup.py` 执行挂载**（普通使用者务必加 `--user`，只装部分加 `--only <a,b>`）：
   - macOS / Linux：`python3 "<dir>/setup.py" --user --init-secrets`（或加 `--only listing-generation,amazon-st-search-terms`）
   - Windows：`py -3 "<dir>\setup.py" --user --init-secrets`（无 `py` 时用 `python`）
   - 若用户本机已有同名非托管目录导致 `SKIP`，**不要擅自加 `--force` 强删**，先向用户说明冲突路径并征求同意。
3. **运行安装后结构化校验**：
   - `python3 "<dir>/setup.py" --user --verify --json`（如指定了 `--only`，带相同 `--only` 参数）。
   - 向用户清晰汇报：已挂载的 Skill、缺少的运行时依赖（如 `openpyxl` 或 Node.js 22+）、以及需要用户自行填写的本地配置文件路径。
4. **🔒 本地隐私文件永不覆盖红线（CRITICAL）**：
   - `~/.config/agent-secrets/**` 与 `~/.web-access/**` 属于用户本机私有数据，位于 Git 仓库之外。
   - **更新或重装时，绝对禁止删除、清空或用仓库内 `templates/` 覆盖任何已存在的本地隐私/配置文件**。
   - `--init-secrets` 仅在目标文件不存在时从模板创建；若模板新增了字段，仅对比提示用户手动补充。

---

## 已收录 Skill

| Skill | 作用 | 适配方式 | 本地隐私配置文件（仓库外，更新永不覆盖） |
| :--- | :--- | :--- | :--- |
| [`listing-generation`](./skills/listing-generation/README.md) | Amazon US/DE Listing 生成与 2026-07-27 规则确定性校验 | 通用 1 份（Win/Mac · Python 标准库） | 无 |
| [`amazon-st-search-terms`](./skills/amazon-st-search-terms/README.md) | Amazon 后台 Search Terms 生成与 250-byte 严格裁剪 | 通用 1 份（Win/Mac · Python 标准库） | 无 |
| [`amazon-ads-optimizer`](./skills/amazon-ads-optimizer/README.md) | Amazon US/DE 广告搜索词报表分析与重建规划 | 通用 1 份（Win/Mac · Python 3.10+） | `~/.config/agent-secrets/amazon-ads-optimizer/products.json` |
| [`web-access`](./skills/web-access/README.md) | 联网研究、事实核实、静态抓取与外部浏览器 CDP 自动化 | 共享 `scripts/` + `agents/{antigravity,codex}/` 差异层（Node 22+） | `~/.web-access/config.env` |
| [`sync-skills`](./skills/sync-skills/README.md) | 仓库作者/Fork 维护者专用：规则体检、脱敏扫描与 GitHub 同步 | 仅维护者模式安装（`--user` 自动跳过） | `~/.config/agent-secrets/sync-skills.env`（可选） |

---

## 手动命令行用法（两种角色区分）

### 角色 A：普通使用者（只读安装与更新，不推送回本仓库）

```bash
# macOS / Linux（按需加 --only listing-generation,web-access）
git clone https://github.com/goEdward/agent-skills.git ~/agent-skills
python3 ~/agent-skills/setup.py --user --init-secrets
python3 ~/agent-skills/setup.py --user --verify
```

```powershell
# Windows (PowerShell)
git clone https://github.com/goEdward/agent-skills.git "$HOME\agent-skills"
python "$HOME\agent-skills\setup.py" --user --init-secrets
python "$HOME\agent-skills\setup.py" --user --verify
```

后续更新（不会触碰任何本地隐私文件）：
```bash
git -C ~/agent-skills pull --ff-only && python3 ~/agent-skills/setup.py --user
```

### 角色 B：仓库作者 / Fork 维护者（多设备双向同步）

运行不带 `--user` 的 `setup.py`，会一并挂载 `sync-skills` 与跨端维护规则 `rules/skills-management.md`：

```bash
git clone https://github.com/goEdward/agent-skills.git <your-local-dir>
python3 <your-local-dir>/setup.py
```

- **改完 Skill 推送到 GitHub**：在 Antigravity 或 Codex 中调用 `sync-skills`（或说“同步 skills”），自动体检全局规则、扫描隐私防泄露并经确认后推送。
- **换设备拉取更新**：运行 `<repo>/skills/sync-skills/platforms/mac/pull-skills.command`（macOS）或 `win/pull-skills.ps1`（Windows）。

---

## GitHub 仓库规范目录结构

```text
<repo>/                        # GitHub 仓库根目录（任意本地克隆路径均可）
├── README.md                  # 仓库总说明 & 给 AI 的安装更新协议
├── AGENTS.md                  # 仓库内开发与脱敏契约
├── setup.py                   # 跨平台一键挂载与体检器（--user / --only / --init-secrets / --verify / --json）
├── .gitignore                 # 防误提交兜底规则
├── .gitattributes             # 强制 LF 换行符，避免 Win/Mac 冲突
├── rules/                     # 维护者全局规则模板（仅维护者模式安装）
│   └── skills-management.md
└── skills/                    # 每个 Skill 一个独立大目录
    └── <skill-name>/
        ├── SKILL.md           # 通用 Skill 定义
        ├── README.md          # 该 Skill 独立说明与隐私隔离说明
        ├── skill.manifest.json# 元数据与隐私模板声明
        ├── scripts/           # 通用跨平台脚本
        ├── references/        # 通用参考文档
        ├── templates/         # 配置模板（绝不含真实隐私）
        ├── agents/            # 按 Agent 差异化覆盖层（antigravity / codex / claude）
        └── platforms/         # 按操作系统差异化辅助脚本（mac / win）
```
