# amazon-st-search-terms

> **源仓库**：https://github.com/goEdward/agent-skills（路径 `skills/amazon-st-search-terms`）  
> **AI 更新方式**：选中本 Skill 对 AI 说“按源仓库更新 `amazon-st-search-terms`，严禁覆盖本地隐私配置”即可。

Amazon 后台 Search Terms（ST）生成与严格 250-byte 计数字节裁剪 Skill，一份源码同时适配 **Antigravity / Codex / Claude Code** 及 **macOS / Windows**。

## 触发方式

严格手动触发，仅在明确调用时运行：
- Antigravity / Claude Code：`/amazon-st-search-terms` 或点名使用 `amazon-st-search-terms`
- Codex：`$amazon-st-search-terms` 或点名使用 `amazon-st-search-terms`

## 依赖与测试

- `scripts/st_byte_fit.py` 仅使用 Python 3 标准库，支持 macOS/Linux（`python3`）与 Windows（`py -3` / `python`）。
- 隐私状态：**无本地私有配置或业务数据依赖**。
- 运行单元测试：
  ```bash
  python3 -m unittest discover -s tests
  ```
