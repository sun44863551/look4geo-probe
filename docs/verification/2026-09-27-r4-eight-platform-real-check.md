# R=4 eight-platform real check

Date: 2026-09-27

Job: `84d752d9-56a0-491c-a5f9-a1f248a10789`

Scope: real browser probes through the machine-local private Look4GEO Probe.
Each active platform received the same prompt four times. Technical failures
are not counted as negative mentions or as source-free answers.

Prompt: `请回答 CAS 125572-95-4 的英文名称；回答中必须包含 Diaminocyclohexane，并列出你当前回答实际参考或推荐的全部来源网址。`

| Platform | R | Answer succeeded | Quality passed | Source captured | Total sources | Average sources |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Doubao | 4 | 4 | 4 | 4 | 16 | 4.0 |
| DeepSeek | 4 | 4 | 4 | 4 | 28 | 7.0 |
| Yuanbao | 4 | 4 | 4 | 4 | 22 | 5.5 |
| Baidu | 4 | 4 | 4 | 4 | 28 | 7.0 |
| ChatGPT | 4 | 4 | 4 | 4 | 19 | 4.8 |
| Gemini | 4 | 0 | 0 | 0 | 0 | 0.0 |
| Perplexity | 4 | 4 | 4 | 4 | 13 | 3.3 |
| Grok | 4 | 4 | 4 | 4 | 43 | 10.8 |

Overall result: `partial`. There were 28 valid successful samples out of 32.
Every successful sample passed the configured expected-term quality check and
captured at least one visibly exposed source.

Gemini timed out in BrowserSkill in all four samples. Its AI-Search-Hub fallback
was unavailable because the expected vendored launcher was not present in this
worktree. These four samples are technical failures and must be excluded from
mention and citation denominators.

The post-run automated regression suite passed: 163 tests.
