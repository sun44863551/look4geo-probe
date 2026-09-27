# Look4GEO Probe seven-platform R=3 verification

Date: 2026-09-27

Scope: real browser probes on the machine-local private runtime. Qwen was explicitly suspended and is not included. Failed technical setup/reproduction batches are retained in the database but excluded from the effective R=3 measurement.

Prompt:

> 请回答 CAS 125572-95-4 的英文名称；回答中必须包含 Diaminocyclohexane，并列出你当前回答实际参考或推荐的全部来源网址。

Expected term: `Diaminocyclohexane`

| Platform | Effective result | Quality | Visible sources by sample | Job ID |
| --- | --- | --- | --- | --- |
| Doubao | 3/3 succeeded | 3/3 passed | 4 / 4 / 4 | `c0954654-4c75-4957-8b7d-0b1bb0052208` |
| DeepSeek | 3/3 succeeded | 3/3 passed | 15 / 5 / 3 | `6dcb11b8-7f47-4c74-bb7d-c02ef6ba051d` |
| Yuanbao | 3/3 succeeded | 3/3 passed | 6 / 6 / 6 | `76f300a2-4998-4c0a-9d7d-b413879399e4` |
| ChatGPT | 3/3 succeeded | 3/3 passed | 4 / 3 / 5 | `c7ffe04b-e125-471e-908d-8c4515241610` |
| Gemini | 3/3 succeeded | 3/3 passed | 3 / 4 / 9 | `0edaecef-0969-4cba-b288-aa9bc4f8084e` |
| Perplexity | 3/3 succeeded | 3/3 passed | 5 / 3 / 5 | `5b359b0b-cdaf-45c9-8391-851a3dc0b577` |
| Grok | 3/3 succeeded | 3/3 passed | 8 / 9 / 8 | `1cf3493f-7fec-426e-9e04-292bc9e5d753` |

Totals:

- 7 platforms tested.
- 21 effective samples.
- 21/21 transport and answer extraction successes.
- 21/21 expected-term quality passes.
- 119 visible source records captured across the 21 samples.
- Qwen remains suspended by user request.

Repairs made during this run:

- Added the current Gemini composer label `Enter a prompt for Gemini`; the earlier technical timeout batch `f55366b6-989a-4a49-bc70-ecf050997dfe` is excluded from the effective sample.
- Added the current logged-in Doubao composer label `发消息或按住空格说话...`.
- Persist running jobs as failed when an active worker is cancelled, instead of leaving a stale `running` state.
- Updated the private local BrowserSkill instance mapping to the currently connected profile.

Interpretation limits:

- A captured source is a source visibly surfaced in the answer DOM or configured source UI. Capture does not independently prove that the model truly used the page internally.
- `quality_status=passed` here means the answer contained the configured expected term and triggered no implemented format warning; it is not a complete scientific fact check of every claim or source.
- Technical failures are not counted as “not mentioned” or as valid measurement samples.
