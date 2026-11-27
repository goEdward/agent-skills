# Public 仓库二次语义脱敏审查清单

自动化正则检查通过后，在确认 `git push` 前，请快速复核以下高风险语义项：

1. **个人身份与本机痕迹**
   - 是否有真实姓名、手机号、微信号、个人邮箱？
   - 是否有本机绝对路径（例如 `/Users/<真实用户名>/...`、`C:\Users\<真实用户名>\...`）？
   - 是否有本机特定浏览器 Profile 名、内网 IP、局域网主机名？

2. **凭据与认证**
   - 是否有 API Key、OAuth Token、Cookie、Session ID、Bearer Header、SSH 私钥或 Webhook URL？
   - 是否有带签名参数的链接（如 `xsec_token=`、`token=`、`sig=`、`key=`）？

3. **业务机密**
   - 是否有未脱敏的店铺名、品牌内部代号、客户名单、SKU 内部编号、采购价/利润率数据或未公开的运营策略？

4. **跨平台 / 跨 Agent 质量检查**
   - 通用 `SKILL.md` 是否避免写死单一 Agent 的私有工具名？
   - 通用脚本是否避免硬编码 `/` 或 `\` 拼接（应使用 `pathlib.Path`）？
   - 新增或修改的 `SKILL.md` 的 frontmatter `name` 是否与文件夹名完全一致？
