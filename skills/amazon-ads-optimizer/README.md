# amazon-ads-optimizer

Amazon 美国站（US）与德国站（DE）广告搜索词报表分析、否定词语义安全审核与重建计划生成 Skill，一份源码同时适配 **Antigravity / Codex / Claude Code** 及 **macOS / Windows**。

## 触发方式

严格手动触发，仅在明确调用时运行：
- Antigravity / Claude Code：`/amazon-ads-optimizer` 或点名使用 `amazon-ads-optimizer`
- Codex：`$amazon-ads-optimizer` 或点名使用 `amazon-ads-optimizer`

## 🔒 本地隐私产品库配置（物理隔离，绝不进 Git）

真实 ASIN / SKU / 客单价 / 毛利率等产品库数据存放在仓库外部的本机固定位置：

- **macOS / Linux**：`~/.config/agent-secrets/amazon-ads-optimizer/products.json`
- **Windows**：`%USERPROFILE%\.config\agent-secrets\amazon-ads-optimizer\products.json`
- 也支持通过环境变量 `AMAZON_ADS_PRODUCT_DB` 或命令行 `--product-db` 指定路径。

新设备初始化时，可运行仓库根目录的 `python3 setup.py --user --only amazon-ads-optimizer --init-secrets`（仅在目标文件**不存在**时由 `templates/products.example.json` 创建），随后填入真实产品数据或从个人加密备份恢复。

> **⚠️ 更新安全声明**：`git pull` 与 `setup.py`（含 `--init-secrets`）**绝不会覆盖已存在的本地 `products.json`**。AI 协助更新时也严禁以模板覆盖该文件。

## 依赖与测试

- Python 3.10+；读取 `.xlsx` / `.xlsm` 报表需要安装 `openpyxl`（`pip install openpyxl`），纯 CSV/TSV 报表无需第三方包。
- 运行单元测试：
  ```bash
  python3 -m unittest discover -s tests
  ```
