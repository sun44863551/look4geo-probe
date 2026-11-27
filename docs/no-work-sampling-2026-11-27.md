# 普通对话/搜索采集与 Perplexity 定位修复

用户要求所有采集不得使用 Work 模式。本约束适用于统一 CLI/MCP 的所有当前浏览器平台，以及可选的 Camoufox Gemini 备用路径。

## 模式约束

- BrowserSkill 在输入前、发送前读取已激活的模式控件。发现 Work、Work mode、Computer、Computer mode、工作、工作模式、电脑模式等已激活标签时拒绝继续；模式读取缺失、截断或被脱敏时也拒绝继续。
- Perplexity 必须显式确认 Search/搜索处于激活状态，且 Computer 未激活；必要时只通过观测到的原生 Search 按钮切回普通搜索。发送前再次验证。
- 豆包保留既有“新对话→输入区对话模式”检查，绝不选择新工作任务。
- Camoufox Gemini 在输入前及每次发送（含 Enter 备用发送）前检查已激活模式控件。
- 模式拦截归类为 send_failed，不重试，不通过适配器链切换路径绕过限制。侧栏中存在 Work、Computer、Projects 等入口不等于这些模式已经启用，不据此判定采集模式。
- BrowserSkill 样本证据保存输入前/发送前的模式检查及激活标签。WorkBuddy 运行时名称与产品 Work 模式无关。

## Perplexity 故障原因与修复

历史 e5341f23 的 Q1/Q2 在并行首次运行及单实例重试中均出现原生点击目标被覆盖/不可用。现场普通 Search 已激活、Computer 未激活，因此没有证据表明此前失败由 Work 模式引起。

当前编辑器标签为 `Ask anything…`，旧精确标签列表缺少该省略号变体，导致定位退回通用 contenteditable 点击。新增 Unicode/ASCII 省略号标签，编辑器限定 role=textbox，每次输入重新观测引用；原生 fill 后读取同一引用，必须与原问题完整一致。发送只使用新观测的 Submit/Send 按钮或精确编辑器的原生 Enter。删除 Perplexity 通用选择器输入回退，保留原生遮挡与控制权保护，不使用脚本写入绕过。

## 验证与部署

真实三实例复测记录见 `outputs/no-work-triple-20261127/`；Perplexity 放在本轮首位，随后 ChatGPT、Gemini Auto。三个实例同一波并行，每个实例内部串行。旧测试记录保持原样，首次结果和重试单独保留。

本地 CLI 每次启动加载新代码。已运行的 MCP 服务需要在没有采集任务时重连才能加载修改；未自动中断现有服务。


验证完成：完整回归 530 passed、5 skipped，日志 `outputs/perplexity-no-work-repair-20261127/full-tests-final.log`。三实例三平台三题首轮 25/27，ChatGPT 两条被浏览器页面变化/交互覆盖保护拒绝；各自独立补测后 27/27。Perplexity 与 Gemini 均为首轮 9/9，未补测；e5341f23 原先缺失的 Perplexity Q1/Q2 均在新一轮首次采集成功。所有 27 条成功记录均保留输入前、发送前两次模式检查；Perplexity 两次均明确为 Search。9 个首轮波次均有三路实际执行重叠，所有隔离和题面一致性检查通过。原始失败记录保留，不回填首轮通过率。
