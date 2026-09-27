# Look4GEO Probe Full Real Matrix — 2026-09-27

## Test definition

- Implementation commit before this report: `5b0e4f7`
- Mode: manual, one platform at a time
- Repeats: `R=1` (engineering verification, not a production baseline)
- Prompt: `请联网查找 CAS 125572-95-4，只列出2个供应商并附上来源链接。`
- Runtime: private local CLI, logged-in BrowserSkill session, local AI-Search-Hub fallback

## Results

| Platform | Job ID | Answer status | Sources | Source status | Result assessment |
|---|---|---|---:|---|---|
| DeepSeek | `5715d905-c76a-4cb7-91b1-3b0570698ade` | succeeded | 2 | captured | Passed. Two answer-linked supplier sources were captured and normalized. |
| Doubao | `d4afbdb6-d131-4ff3-8227-1928b96e1120` | succeeded | 4 | captured | Technical capture passed, but business answer failed quality review: the CAS was mapped to the wrong compound and non-standard hyphens caused visibly split/duplicated URLs. Do not use as a reliable measurement answer. |
| Yuanbao | `82cdc721-8e37-43ff-9261-1b9340fddcf7` | succeeded | 2 | captured | Passed. Complete answer and two supplier links captured. |
| Qwen | `e8a66c3c-6374-478a-992f-428db88f29e3` | failed | 0 | none_exposed | BrowserSkill timed out and AI-Search-Hub also timed out. Exclude from mention/citation denominators. |
| ChatGPT | `86946b22-5f9d-4f08-9cc8-b7d79819aabd` | succeeded | 3 | captured | Passed. Complete answer and three answer-linked sources captured, including the Hanyu website. |
| Gemini | `0817f827-5a90-4c3e-8c78-187a605a1f79` | succeeded | 2 | captured | Passed directly through BrowserSkill; no fallback required. |
| Perplexity | `aeb4a157-bd18-43e6-856f-d2c5c661d322` | succeeded | 2 | captured | Passed. The selected main answer and its two sources were captured without follow-up contamination. |
| Grok | `cb84c0e0-eae7-4733-a8f8-385cd2b3c76e` | succeeded | 2 | captured | Passed. Invisible source-label separators were excluded from normalized URLs. |

## Summary

- Transport/answer success: 7/8 platforms.
- Clean business-answer and source success: 6/8 platforms.
- Technical capture succeeded but answer quality was unacceptable: Doubao.
- Failed/blocked: Qwen (two bounded adapter timeouts).
- No failed platform was converted into “not mentioned” or a zero result.
- All successful source records in this run were `cited` / `answer_dom`; no additional panel-only `surfaced` sources were produced.

## Findings requiring follow-up

1. Qwen remains unusable for this measurement until its browser submission/extraction flow or upstream AI-Search-Hub path is repaired.
2. The final Qwen attempt reports a failed answer with a timeout diagnostic, but the fallback attempt's `failure` field is null. The status remains correctly failed; failure-kind propagation should be hardened separately.
3. Doubao's current answer can be syntactically captured while factually wrong. Production GEO collection needs a domain-validity gate or review state so “captured” is not confused with “factually trustworthy.”
4. Doubao emitted Unicode non-standard hyphens inside URLs. These links should be treated as visible platform evidence, not as verified reachable destinations.

## Automated regression

The project-level pytest configuration now disables only pytest's optional cache provider in restricted runners. The ordinary command remains:

```text
.venv/bin/pytest -q
```

The cache setting does not affect probe execution, result storage, BrowserSkill, AI-Search-Hub, or Promptfoo.
