# Baidu platform replacement verification

Date: 2026-09-27

## Scope

Qwen was removed from the active Look4GEO Probe platform registry and Baidu AI
was added under the platform ID `baidu`. Baidu uses the existing unified local
CLI/MCP surface and the `browser_skill` adapter; no second tool was introduced.
Historical Qwen result data and verification documents remain readable.

## Automated verification

- Full suite: 163 tests passed.
- Active platform registry: Doubao, DeepSeek, Yuanbao, Baidu, ChatGPT, Gemini,
  Perplexity, and Grok.
- Qwen is absent from runtime and adapter configuration.

## Real browser verification

Job: `a8a48125-e396-4028-9557-6ea69dde97b9`

Prompt: `请回答 CAS 125572-95-4 的英文名称；回答中必须包含 Diaminocyclohexane，并列出你当前回答实际参考或推荐的全部来源网址。`

| Sample | Answer status | Quality | Source capture | Sources | Citations |
| --- | --- | --- | --- | ---: | ---: |
| 1 | succeeded | passed | captured | 5 | 5 |
| 2 | succeeded | passed | captured | 5 | 5 |
| 3 | succeeded | passed | captured | 7 | 7 |

Distinct visible source domains were `www.chemicalbook.com`,
`www.ichemistry.cn`, `www.jkchemical.com`, `www.lobachemie.com`, and
`www.sigmaaldrich.com`.

An earlier sandbox-denied BrowserSkill connection was retained as failed job
`d70bec50-3704-4d62-b4c9-d839d89b3cd3`. It is a technical failure and is not
included in the effective R=3 sample above.
