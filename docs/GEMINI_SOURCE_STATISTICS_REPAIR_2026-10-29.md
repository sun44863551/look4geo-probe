# Gemini 信源统计修复（2026-10-29）

## 修复范围

需要更新 Probe，而非只更改显示名称。旧代码将答案区域购物链接记作引用，客户统计又仅选 cited，因此购物入口占据引用表、已采集的来源面板网站未展示。保留原始链接，按用途与证据位置两个维度分别统计；不根据商品名称猜测真实商家网址。

新任务输出 schema 4。每个来源增加 `source_kind`：website / shopping_entry / search_entry / hosted_unresolved。非网站内容入口降为 surfaced，仍保留 `linked_in_answer` 与原始证据位置，不进入 citations。Google 开发文档、支持文档仍可作为网站来源，不进行整个 google.com 的域名封禁。

每次尝试增加 `source_statistics`，网站 cited 与 surfaced 分开；购物入口、搜索入口、发布者未确认链接单列。未捕获证据时为 null；不得将失败或未暴露当作已测量零。历史 schema 1–3 可以读取，读取时派生分类不写回原文件；不隐式迁移运行数据库。

## 统一离线重统计入口

```sh
./scripts/probe-local source-report /absolute/path/to/probe/raw \
  --platform gemini --output /absolute/path/to/new-report.json
```

输出包含全部链接、分项条数、覆盖问题数、网站主机汇总、每题状态及输入 SHA-256。输出路径必须是新文件，父目录须存在。该命令不启动浏览器、不请求模型、不重跑问题，不覆盖客户原始文件或旧报告。

网站主机汇总只移除 www.，不将任意子域误合并为同一家发布者。各类问题覆盖数可能重叠，不能相加。网页外链不等于已人工验证的正文引用；原始 answer_dom 的普通外链保留其历史 cited 标记，不作额外真实性背书。

## 验证

- 首轮新增分类回归测试：修复前 10 失败、1 通过，失败复现购物/搜索入口进入引用列表、缺少分项统计。
- 新离线入口测试：实现前 2 失败，分别验证命令缺失、统计模块缺失；实现后通过。
- 完整项目测试：384 项通过（2026-10-29）。
- 最新 700 份 Gemini 真实历史记录经本机 CLI 离线回放：3184 条来源 = 网站原 cited 外链 2 + 网站 surfaced 696 + 购物入口 2480 + 发布者待确认 6；普通搜索入口 0。
- 来源捕获状态：captured 536，none_exposed 164。696 条面板网站来源不被升级成正文引用。
- 现场未新增线上问答；此验证是实际已采数据回放，不是新一轮在线 R=3。

## 迁移与边界

CLI 可直接使用已修改的本机源码；长驻 MCP 客户端需重新连接以加载新代码。浏览器实例、账号、Gemini Flash 和 Perplexity Search 配置均未改变。

客户旧脚本若仍只统计 cited，不能表达“全部信源”。改用新离线报告，或按 source_kind=website 同时导出 cited/surfaced 并分别展示。旧 JSONL 中 citation_urls 可能包含全部 sources，旧 CSV 又只包含 cited，两者不应直接互作分母。旧客户报告未自动覆盖，旧 ZIP 快照亦未替换；不能将旧 Google 占比结论继续当作实际网站信源分布。
