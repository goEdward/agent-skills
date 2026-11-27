# listing-generation

> **源仓库**：https://github.com/goEdward/agent-skills（路径 `skills/listing-generation`）  
> **AI 更新方式**：选中本 Skill 对 AI 说“按源仓库更新 `listing-generation`，严禁覆盖本地隐私配置”即可。

Amazon 美国站（US）与德国站（DE）Listing 生成与确定性校验 Skill（符合 2026-07-27 新标题与商品亮点规则），一份源码同时适配 **Antigravity / Codex / Claude Code** 及 **macOS / Windows**。

## 触发方式

严格手动触发，仅在明确调用时运行：
- Antigravity / Claude Code：`/listing-generation` 或点名使用 `listing-generation` skill
- Codex：`$listing-generation` 或点名使用 `listing-generation` skill

## 依赖与校验

- 全部内置脚本（`scripts/*.py`）仅依赖 Python 3 标准库（零第三方包），支持 macOS/Linux（`python3`）和 Windows（`py -3 -X utf8`）。
- 隐私状态：**无本地私有配置或业务数据依赖**。
- 自检测试：
  ```bash
  python3 scripts/selftest.py
  ```
