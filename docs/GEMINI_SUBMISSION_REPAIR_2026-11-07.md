# Gemini 发送失败诊断与修复

## 当天运行情况

读取本机 SQLite 记录，以及客户项目的正式批次日志、主进度与 B-50 记录。客户记录显示 ChatGPT 与 Perplexity 各完成 119 格，Gemini 为 0/119。最近两次 Gemini 失败报告为发送状态三次未变化。此处说明当时记录中的结果，客户批次的补跑另行执行。

## 已修复

发送确认脚本中，Python 字符串拼接丢失了 JavaScript 的空字符串引号，实际生成 `composer.textContent || ) : ;`。新增测试用 Node 执行真实生成的脚本，修复前出现语法错误，修复后通过。

发送后出现 `Something went wrong (1)` 时，现在保留该平台错误并立即结束本次发送尝试，结果为 `send_failed`。避免把它记录成不明超时并连续重复发送。输入框节点消失时，也不会仅因为文本为空而确认发送成功。

## 网页实测

所有新建会话均显式绑定 `81483588`，分别核对了 Flash 与 Flash-Lite。使用中性诊断题，证据保存在本机忽略目录 `work/verification-20261107-gemini-submission-fix`。

记录到可信的 beforeinput、input、Enter 和发送按钮点击事件。原生填入后的 DOM 为 227 字符，Quill 文本模型为 228 字符，差额是编辑器末尾换行。模型文本与原题一致，发送后仍显示 `Something went wrong`，没有新增问题记录。

对照了 BrowserSkill 的 `--no-clear`、标准编辑命令，以及网页已有 Quill 的公开文本方法。以上对照没有恢复问答。标准编辑命令触发控制权撤销时立即停止该尝试。诊断脚本和失败结果分开保留。

16:32 的正常 CLI 短题为 `Reply with exactly GEMINI WEB OK.`。当时默认 Flash 返回 `send_failed`，诊断为 `Gemini platform error: Something went wrong (1)`，没有有效正文。此处记录的是恢复前的失败。最新验收见下文。

## 验证与使用

完整测试为 **426 passed**，`git diff --check` 通过。最终 BrowserSkill 会话列表为空。此前 ChatGPT 修复及其他本机未提交工作继续保留在当前工作目录。

新启动的本机 CLI 会加载当前代码。检查时存在两个已启动约 20 小时的本地 MCP 服务进程，WorkBuddy 通过旧进程调用时需要重新连接本地 probe 服务才能加载修复。

## 手工正常后的复验

用户确认同一采样 Profile 的短题手工回答正常。17:19 检查时，81483588 已重新连接。修复后的本机常规入口以默认 Flash 新建三次独立问答，结果全部通过。

| 样本 | 正文字符数 | 链接记录 | 排除示例域名后的实际网站来源 | 信源状态 |
|---|---:|---:|---:|---|
| R1 | 1,768 | 3 | 2 | captured |
| R2 | 2,118 | 3 | 2 | captured |
| R3 | 2,027 | 4 | 3 | captured |

以上正文长度均按字符统计。三轮问题是同一条要求联网、比较 robots.txt 与 sitemap 并给出来源的中性测试题。独立短题也精确返回 `GEMINI WEB OK.`，该短题没有展示信源。记录在 `work/verification-20261107-gemini-after-manual-r3` 与 `work/verification-20261107-gemini-manual-comparison`，均属于本机忽略目录。

三轮 job_id 为 `e57e616e-5868-49b9-b4f2-0441e11c8e6b`、`f27b50a1-42ca-4921-91f0-914d823e47c0`、`bcdb431d-b45a-4658-874f-60a4bff44326`。最终 BrowserSkill 无测试会话残留。

这次验收证明当前页面状态下，修复后的 CLI 能完成问答和信源采集。当前两个长驻 MCP 服务仍早于此次代码修复，WorkBuddy 需重连本地 probe 服务后使用修复版。客户正式批次状态仍以其单独运行记录为准。

## 参考

- BrowserSkill 对编辑器输入实现的维护者说明，描述了原生插入与清空步骤。[Issue 145](https://github.com/Tencent/BrowserSkill/issues/145)
- Quill 的公开编辑器文本接口。[官方文档](https://quilljs.com/docs/api)
