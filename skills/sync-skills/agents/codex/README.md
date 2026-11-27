# Codex 适配说明

`sync-skills` 当前完全使用上层通用 `../../SKILL.md`，无需单独维护 Codex 专属版本。

## 如何启用专属覆盖（未来按需）

如果后续希望为 Codex 定制专有提示词或触发规则：

1. 在本目录创建 `SKILL.md`（frontmatter 保持 `name: sync-skills`）。
2. 在仓库根目录运行 `python3 setup.py`。
3. `setup.py` 会自动将 `~/.codex/skills/sync-skills` 链接切换到本目录版本，且不会改动其他 Skill。
