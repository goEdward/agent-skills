# sync-skills

用于自检全局规则、校验规范、脱敏扫描并安全同步跨平台多 Agent 技能库到 GitHub 的独立 Skill。

## 适用范围与目录说明

| 子目录 / 文件 | 作用 | 读取方 |
| :--- | :--- | :--- |
| `SKILL.md` | 通用核心工作流（含首次运行全局规则自检） | Antigravity / Codex / Claude Code |
| `scripts/sync_tool.py` | 纯 Python 3 跨平台体检与同步脚本（自动定位所在仓库） | 所有平台（macOS / Windows） |
| `references/security-checklist.md` | Public 仓库二次语义审查清单 | 所有 Agent |
| `templates/config.example.env` | 本地隐私配置模板（复制后放到仓库外） | 人类配置参考 |
| `agents/antigravity/` | Antigravity 专属说明或未来覆盖入口 | Antigravity |
| `agents/codex/` | Codex 专属说明或未来覆盖入口 | Codex |
| `agents/claude/` | Claude Code 预留目录 | Claude Code |
| `platforms/mac/` | macOS 一键拉取辅助脚本（`pull-skills.command`，自动定位仓库） | macOS |
| `platforms/win/` | Windows 一键拉取辅助脚本（`pull-skills.ps1`，自动定位仓库） | Windows |

## 本地隐私配置（可选，强烈建议 Public 仓库启用）

将 `templates/config.example.env` 复制到仓库外部的固定路径：

- **macOS / Linux**：`~/.config/agent-secrets/sync-skills.env`
- **Windows**：`%USERPROFILE%\.config\agent-secrets\sync-skills.env`

在其中填写 `CUSTOM_REDACT_TERMS`（如你的真实姓名、本机用户名、公司/品牌/店铺敏感词），每次同步前脚本都会自动扫描拦截，且该文件绝不会进入 Git。

## 快速命令参考

```bash
# 体检：检查 Antigravity / Codex 全局规则与本机挂载状态
python3 skills/sync-skills/scripts/sync_tool.py doctor

# 查看仓库状态
python3 skills/sync-skills/scripts/sync_tool.py status

# 仅检查本次变更的规范与隐私安全
python3 skills/sync-skills/scripts/sync_tool.py audit

# 扫描整个仓库
python3 skills/sync-skills/scripts/sync_tool.py audit --all

# 拉取远端更新并刷新本机挂载与全局规则
python3 skills/sync-skills/scripts/sync_tool.py pull --refresh-setup
```
