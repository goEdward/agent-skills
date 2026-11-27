# Antigravity 适配说明

`sync-skills` 当前完全使用上层通用 `../../SKILL.md`，无需单独维护 Antigravity 专属版本。

## 如何启用专属覆盖（未来按需）

如果后续希望为 Antigravity 定制专有能力（例如调用 `generative_ui` 渲染交互式提交确认面板或关联自定义 Hook）：

1. 在本目录创建 `SKILL.md`（frontmatter 保持 `name: sync-skills`）。
2. 在仓库根目录运行 `python3 setup.py`。
3. `setup.py` 会自动生成 `.local-views/antigravity/sync-skills` 并更新 `~/.gemini/config/skills.json`，优先加载本目录版本，而不影响 Codex 等其他 Agent。
