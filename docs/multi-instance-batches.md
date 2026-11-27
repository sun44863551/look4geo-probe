# 同一客户的两种多实例采集方式

统一入口为 `scripts/probe-local batch`。每次批次必须明确客户 ID、批次 ID、浏览器实例、平台和题库。三个实例均使用独立配置、数据库、锁和证据目录，所有结果汇入同一客户批次的 `report.json` 与 `REPORT.md`。

## 两种调度

| 配置 schedule | 执行方式 | 三实例、三平台、三题的样本数 |
|---|---|---:|
| `same-platform` | 同一平台、同一道题由三实例同时采集；该轮结束后换题，平台完成后再换平台 | 27 |
| `platform-per-instance` | 每个实例固定负责一个平台，三路同时开始，随后各自按题库串行推进 | 9 |

同平台模式产生的是三个不同实例的独立样本，不将它们合并成一个回答。固定平台模式中，较快的平台可以继续下一题，不必等待较慢的平台。每个实例内部始终串行。

模板：

- `config/batches/same-platform.example.json`
- `config/batches/platform-per-instance.example.json`

两个模板均使用客户 `validation-demo`、相同三道技术问题及已授权的三个实例，仅用于验证。正式使用时复制模板，填写真实客户 ID、全新的批次 ID 和题库。`customer_id`、`batch_id`、题目 `id` 使用字母、数字、点、下划线或短横线，客户名称可在外部客户台账中对应。

固定分配模板为：

| 实例 | 平台 |
|---|---|
| `7cf212dd` | ChatGPT |
| `9ac92006` | Gemini Auto |
| `e5341f23` | Perplexity |

`assignments` 必须覆盖所有实例，且每个平台恰好分配一次；同平台模式不填写 assignments。Gemini 使用用户已选定的 `auto`。买家语言基线显式保留，语言验证的含义与普通 run 命令相同。

## 使用

先检查配置及样本数量；此操作不会连接浏览器或发送问题：

```sh
scripts/probe-local batch --spec config/batches/same-platform.example.json --plan --json
scripts/probe-local batch --spec config/batches/platform-per-instance.example.json --plan --json
```

分别执行，输出目录首次执行必须为空。两个批次使用相同实例，因此按顺序运行：

```sh
scripts/probe-local batch --spec config/batches/same-platform.example.json --output outputs/customer-demo-same --json
scripts/probe-local batch --spec config/batches/platform-per-instance.example.json --output outputs/customer-demo-split --json
```

输出：

- `spec.json`：不可混用的客户/批次/调度/题库配置。
- `cells/*.json`：逐条记录，包含客户、批次、实例、平台、题号、原题、时间及完整 CLI 结果。
- `instances/<browser>/`：对应实例专用 local.env、数据库、浏览器操作轨迹及信源证据。
- `timeline.jsonl`：批次和样本的实际启动/结束时间。
- `report.json`、`REPORT.md`：本客户批次最新汇总及原始记录索引。

程序禁止静默替换断连实例；同一实例不能同时归属两个通过此入口运行的客户批次。不要与旧版手写采集器或其他手动浏览器采集混跑。不会修改默认 `config/local.env`。CLI/MCP 单条采集接口保持原有行为；目前批量调度使用本 CLI 入口。

## 恢复与补采

用相同配置和输出目录再次运行会复用成功、非空、题面一致且信源采集完整的记录。失败会暂停所属实例，其他实例可以继续；失败不会计为“品牌未提及”。退出状态为 1 表示尚未全部成功，详细状态在报告中。

默认不会重发失败请求。先核对诊断、浏览器状态和原始轨迹；确认可以重新采集后，显式使用 `--retry-failed`。之前的失败 JSON/stdout/stderr 会移至 `history/`，成功记录仍复用。报告为最新结果，首次并发成功率必须结合 history 和 timeline 单独计算，不能把补采计入首轮。

进程中断留下的 `.pending.json` 表示上次动作尚未确认；即使指定 `--retry-failed` 也不会重发。先人工核对会话、确认原操作状态并处理检查点，再决定恢复，避免盲目重复。

## 模式限制

只采普通对话/搜索，禁止 Work/Computer。沿用各平台输入前、发送前的模式检查，Perplexity 必须确认 Search，豆包必须确认对话；每次采集进程均加载最新代码。详见 `docs/no-work-sampling-2026-11-27.md`。


## 2026-11-27 实测结果

新统一入口实测：同平台模式（三实例、三平台、一题）首轮 9/9；固定平台模式（三实例各负责一个平台、三题）首轮完整 7/9，ChatGPT Q2 引用浮层提取失败后暂停该实例，检查并显式补采后 9/9。原失败及补采日志分别保留，未回填首轮通过率。

18 条最终成功样本的客户归属、题面、实例绑定、数据库、证据路径、实例内串行以及输入前/发送前模式检查均通过核对；实测两种调度均存在真实并发。成功检查点恢复没有新增 job，也未修改原样本文件。完整回归 543 项通过、5 项跳过。详见 `outputs/dual-schedule-20261127/REPORT.md`。
