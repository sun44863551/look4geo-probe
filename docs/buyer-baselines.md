# 买家语言基线

请求参数 `buyer_baseline`：`english`（默认）、`japan`、`korea`。
分别请求 en-US、ja-JP、ko-KR。问题原文不附加回答语言指令。
问题本身是日文或韩文，不代表选择了对应买家基线；未明确指定时仍默认英语。

CLI：`scripts/probe-local run "原始问题" --mode manual --platform chatgpt --buyer-baseline japan --json`
MCP：`probe_run(prompt="原始问题", platforms=["chatgpt"], mode="manual", buyer_baseline="korea")`。

当前只有 ChatGPT BrowserSkill 通道验证浏览器和页面语言。
结果中的 `buyer_baseline` / `buyer_locale` 是请求配置；只有
`locale_verified=true` 才是经过检查的页面语言基线。
其他平台、旧记录或其他适配器的 null 表示未验证，不得作为对应买家基线成功样本。
语言设置不改变 IP、地理位置，也不保证答案正文语言或每个信源标题语言。

WorkBuddy/Codex 应将“采用日本买家基线”映射 japan，
“采用韩国买家基线”映射 korea；未说明映射 english。
批量任务每次调用都传递相同参数，检查点键包含基线，不混用历史英语样本。

## 验证记录（2026-10-05）

指定 BrowserSkill 实例 81483588 的真实 ChatGPT 最小问答：
日本基线返回「テスト成功」，韩国基线返回「테스트 성공」，
英语基线返回「Sampling test OK」；三次 status 均 succeeded，locale_verified 均 true。
这是语言设置与问答抽取测试，不是 100 条批量或信源标签的全面验证。
本地 WorkBuddy 私有技能已加入自然语言到参数的映射规则；
尚未在 WorkBuddy 对话内执行完整批量验收。
