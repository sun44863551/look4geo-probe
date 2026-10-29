# Look4GEO Probe 本地部署说明（2026-10-28 快照）

## 版本与适用范围

本包是当前本机工作目录的源码快照：Git 基点 `b275ce35ffa536cde99c74d4648b3644cb89219c`，**包含其后尚未提交的修复**，并非该提交的原样导出。Python 项目版本仍为 `0.1.0`；快照身份以 `VERSION.json` 和 `MANIFEST.json` 为准。

目标：本机私有运行，由 Codex / WorkBuddy 调用同一个工具，自主选择平台，使用网站问答而非模型 API。本包不安装到公共技能市场，不上传采集内容。

2026-10-27 最近真实测试：ChatGPT、Gemini Flash、Perplexity 普通 Search 各 R=3 成功；详见 `docs/verification-2026-10-27-overseas-r3.md`。这不代表其他平台在此次也完成了 R=3，亦不保证网站改版或账号限额后的持续可用性。

包含最新 Gemini 原生填入 / Enter 优先提交、Flash 就绪等待、Perplexity 停止响应检测、Google 文档信源过滤兼容修复，以及本地入口浏览器实例校验。附带已应用现有补丁的 AI-Search-Hub 源码，不需要 Hermes。

## 1. 解压与环境准备

建议解压至自己可写、路径稳定的独立目录；不要覆盖正在运行的旧安装。当前部署流程面向 macOS；其他系统尚未在本快照上验证。

准备 Python **3.12.x**（本项目不支持 3.13）、Node.js **22.22+**、Git、Chrome/兼容 Chromium，以及 BrowserSkill 扩展和 `bsk` CLI。`bsk` 必须在调用方能访问的 PATH 中；本包不含这些运行环境或安装程序。

在解压目录打开终端：

```sh
python3.12 --version
node --version
bsk --help
python3.12 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
```

安装依赖需要网络。ZIP 是源码部署包，**不是离线依赖全集**；不会携带旧机器的 `.venv` 或 `node_modules`。

不要对本 ZIP 直接执行 `scripts/bootstrap.sh`：历史脚本需要 Git 子模块元数据，还会安装可选 Promptfoo，而 ZIP 不含 `.git`。上面的手动安装路径不依赖 Git 元数据。

## 2. 隔离采样账号与绑定浏览器

打开隔离的 Chrome Profile **Look4GEO Sampling**，连接 BrowserSkill 扩展，并在此 Profile 手动登录 ChatGPT、Gemini、Perplexity 等需要采集的网站。请勿导入日常 Profile 的 Cookie；本包也不含任何登录态。

当前本机指定实例为 **81483588**。在另一台电脑或重新创建 Profile 后，实例 ID 可能不同，需要用户确认新的采样实例，不能照搬旧 ID 或自动改用默认浏览器。

```sh
cp config/local.env.unified.example config/local.env
```

同机保持模板中的四个 ID 为 `81483588`；迁移时将四处全部改为用户确认的新实例 ID。Gemini 本部署走 BrowserSkill，Camoufox 已禁用，不需要另装 Camoufox 浏览器。

检查指定实例是否能连接（此命令会创建会话，请记下返回的会话 ID，并按 `bsk session --help` 的说明在检查后关闭）：

```sh
bsk session start --browser 81483588 --json
```

若实例未连接，停止采集并重新连接同一 Profile；**不得省略 `--browser` 或切换到其他实例**。

Gemini 统一选择 **Flash**，不用 Pro 模式；订阅名称中有 Pro 不等于必须使用 Pro 模型。Perplexity 使用普通 **Search**，不使用 Pro Search。

## 3. 检查与最小测试

```sh
chmod +x scripts/probe-local
./scripts/probe-local platforms --json
./scripts/probe-local doctor --json
.venv/bin/python -m pytest -q
```

`doctor` 会同时检查可选组件，可能因未安装 Promptfoo/Camoufox 等报告不完整；需阅读具体检查项，不能将可选组件缺失等同于 BrowserSkill 三平台不可用。部分历史测试依赖原机器外部文件或 Node 环境，在新机器可能跳过；不应为测试去复制客户目录。

先只测一个平台、R=1，确认答案与信源后，再逐个平台做 R=3：

```sh
./scripts/probe-local run 'Use web search to explain robots.txt and cite two authoritative sources with URLs.' --mode manual --platform gemini --repeats 1 --timeout 300 --json
```

将 `--platform` 换为 `chatgpt` 或 `perplexity` 即可。R=3 使用 `--repeats 3`。不要并发启动多个调用者对同一 Profile 重复提交，不要在本次采集未结束时再次补交同一个任务。

买家基线默认英文；日本、韩国分别添加 `--buyer-baseline japan`、`--buyer-baseline korea`。问题语种不能代替界面语言验证；尤其不要把 Gemini/Perplexity 未验证的界面语言当作已验证的买家基线。

结果保存在当前安装目录 `data/`，包括 SQLite 与每次运行证据。未成功、限流、未登录、超时或抽取失败不能记作“未提及”；成功也不意味着可获取平台未公开的全部隐藏信源。

## 4. Codex / WorkBuddy 调用

最简单且一致的方式：让两者都调用本地绝对路径 `项目目录/scripts/probe-local`，先读 `DEPLOYMENT.md`，显式选择平台。此入口会读取 `config/local.env` 并执行实例锁定检查。

例如向任一工具下达：

> 使用本机 Look4GEO Probe 私有入口，仅测 Gemini，Flash 模式，R=3；绑定采样实例 81483588，断连时停止，不改用 API，不切换其他 Profile。返回任务状态、原始回答与可见信源。

已有本地私有技能或 MCP 连接的用户：将其中的旧项目路径改成解压后的绝对路径，并重启/重新连接客户端，避免旧进程继续运行旧代码。`connectors/` 中是接入参考，不是自动安装程序；不要复制到公共技能区。

可选 MCP 示例见 `deployment/mcp.unified.example.json`：将其中两处 `/ABSOLUTE/PATH/TO/Look4GEO-Probe` 替换为真实目录，并将 PATH 改为本机 `bsk` / Node 所在目录。把 `mcpServers` 条目合并进客户端支持的配置格式，不要覆盖整份个人配置。

注意：直接启动 `look4geo_probe.mcp_server` 不会读取 `config/local.env`，需要在 MCP 的 env 中明确配置所有浏览器 ID 和禁用 Camoufox。示例中设置实例锁定值只是表达相同配置；**入口的严格 ID 一致性检查只由 `scripts/probe-local` 执行**。要求严格锁定时优先使用 CLI 入口，并同步修正所有客户端环境配置。

## 5. 可选组件与常见问题

- **Promptfoo**：仅用于可选批量评测，不是三个网站问答的必要条件。需要时另行安装，API 评测不在本次默认部署范围内。
- **AI-Search-Hub**：随包附带的是上游当前子模块源码及已有本地补丁，来源与提交见 `VERSION.json`；保留上游说明。当前三个海外平台的已验证路线是 BrowserSkill，不能据此宣称其他适配器也验证通过。
- **找不到 bsk / node**：先确保终端能运行；GUI 客户端可能不继承终端 PATH，要在客户端启动环境显式配置可执行文件所在目录。
- **限流**：暂停该平台，按网站提示等待恢复后只补失败样本；降低串行频率。工具不能保证解除账号或平台风控，不用换账号/切换实例规避限制。
- **Gemini 无窗口**：检查绑定 Profile 是否打开、扩展是否连到正确实例；这里不使用独立 Camoufox 窗口。
- **迁移部署**：必须重新登录。ZIP 不含账号配置、Cookie、浏览器 Profile、客户数据、运行数据库、原始采集结果或错误日志。
- **回退**：保留旧安装目录；停止新任务后，调用方改回旧目录即可。不要将新旧版本同时指向同一个运行中的任务。

## 6. 完整性

`MANIFEST.json` 列出包内载荷文件的 SHA-256；它自身不包含在该清单中。ZIP 外部 `.sha256` 文件用于校验下载后的整个 ZIP。版本清单区分 Git 基点与工作目录快照，避免误认为所有改动已提交或推送。
