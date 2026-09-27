# Visible Source Capture Verification — 2026-09-26

## Scope and tested revision

- Branch: `codex/visible-source-capture`
- Feature revision tested: `1b04d9eb3d800ccced33951ce078c79bea8e3e4c`
- V1 rollback tag: `look4geo-probe-v1` (`dbf61ba`)
- Runtime: private local checkout on the user's Mac mini, using the existing logged-in Chromium/BrowserSkill session
- Probe prompt: a bounded supplier search for CAS 125572-95-4 requesting source links
- Repeats: 1 per platform for engineering verification only

The source boundary is deliberately narrow: `sources` contains links visibly exposed by the product UI or present in the answer text returned by an existing upstream adapter. It does not claim to reveal private model context or sources that the product did not expose.

## Automated gates

- Complete suite after all live-test fixes: **147 passed**.
- Validity and security gate: **7 passed**.
- Environment doctor: passed for Python 3.12, Node, Git, BrowserSkill CLI, Chrome, AI-Search-Hub and Promptfoo. Docker is absent but optional.
- Codex and WorkBuddy skill validation: both valid.

Commands used included:

```text
.venv/bin/pytest -q
.venv/bin/pytest -q tests/test_validity.py tests/test_security.py
.venv/bin/python scripts/doctor.py --json
scripts/probe-local run "<prompt>" --mode manual --platform <platform> --repeats 1 --json
```

## Eight-platform real-browser matrix

All result records are stored in the private local database at `data/probe.sqlite3`. Counts below are deduplicated `SourceRecord` counts from the final bounded attempt.

| Platform | Job ID | Answer | Cited | Surfaced | Total | Source status | Audit / concise diagnostic |
|---|---|---:|---:|---:|---:|---|---|
| DeepSeek | `c4e9c206-1352-46fc-a11b-5d80f0bfa120` | succeeded | 9 | 0 | 9 | captured | Browser answer and visible citation links matched the stored records after trimming list separators. |
| Doubao | `97c6d47c-0f12-4a97-b263-6e6cf4fedbbe` | succeeded | 9 | 0 | 9 | captured | Stored links matched links visibly exposed by the answer UI. Some platform-generated URLs used non-standard hyphens and were visibly split/malformed; preserve as platform-output evidence, not verified destinations. |
| Yuanbao | `92804d90-0beb-4d1e-aef8-84454ecc1167` | succeeded | 14 | 0 | 14 | captured | Full answer and its explicit source-link section matched after fixing nested source URLs being selected as the main answer. |
| Qwen | `ae86b454-b1d3-499c-8b0e-0eccabcfe471` | failed | 0 | 0 | 0 | none_exposed | BrowserSkill timed out; AI-Search-Hub returned only the landing-page placeholder. Correctly classified `extraction_failed`, not a successful answer or a zero-mention sample. Artifact: `vendor/AI-Search-Hub/out/qwen_answer.txt`. |
| ChatGPT | `b1e628bf-6e74-4815-860d-393739c9a08c` | succeeded | 13 | 0 | 13 | captured | Selected answer and inline citations matched the visible answer DOM. |
| Gemini | `e5bcdf74-38b7-454d-8f7a-2f1db8dc273d` | succeeded | 0 | 0 | 0 | none_exposed | BrowserSkill timed out; AI-Search-Hub returned a complete answer but no link URLs in its output. The saved upstream artifact matches the stored answer, but separate Gemini source cards were not captured. Artifact: `vendor/AI-Search-Hub/out/gemini_answer.txt`. |
| Perplexity | `ad7e7b71-967d-4ded-b50d-d7ff31c3cfed` | succeeded | 11 | 0 | 11 | captured | Longest answer region and its answer-scoped links matched; follow-up suggestions were excluded. |
| Grok | `8f825767-4e97-4c56-9fd3-7da58664d122` | succeeded | 11 | 0 | 11 | captured | Answer and visible links matched after treating the platform's invisible source-label separator as a URL boundary. |

No failure in this matrix was converted into “not mentioned.” Qwen must remain excluded from measurement denominators until a valid answer is obtained.

## Local caller smoke tests

Both callers used the same repository-local implementation and command shape; no public WorkBuddy skill or cloud connector was installed.

| Caller | Job ID | Result | Source fields |
|---|---|---|---|
| Codex | `53d5e2fd-31bc-46b1-a22f-e5695708b166` | DeepSeek returned `Codex local caller OK` | `citations`, `sources`, `source_capture_status`, and `source_capture_diagnostic` present |
| WorkBuddy | `71ec3fc2-4169-4d03-92b5-1317ed8932be` | DeepSeek returned `WorkBuddy local caller OK` | Same schema v2 fields from the same local CLI |

## Defects found and fixed during live verification

1. DeepSeek answer-text URLs could absorb a trailing list separator, producing duplicates. Fixed with a context-aware URL boundary test.
2. Yuanbao could select a nested source URL instead of the complete answer. Fixed by selecting the longest valid answer candidate.
3. AI-Search-Hub could treat a Qwen landing-page placeholder as success. Fixed by requiring a valid measured answer and returning `extraction_failed` otherwise.
4. Grok could append an invisible source-label separator and label to a text URL. Fixed by stopping URL extraction at Unicode format separators.

Every fix was introduced with a failing regression test and followed by the complete suite.

## Known limitations and follow-up

- Qwen is not currently usable for valid measurement in this browser state. It requires selector/login/age-verification investigation; no verification challenge was bypassed.
- Gemini currently succeeds through the existing AI-Search-Hub fallback when BrowserSkill times out, but that adapter does not expose Gemini's separate citation cards. This run is therefore valid for answer capture, not for a positive citation-rate claim.
- `surfaced` remained zero in the final matrix because the successful answers exposed usable links inline. Panel-only capture logic is covered by fixtures and automated tests, but no final run produced additional panel-only URLs.
- The Doubao response included malformed/non-standard URL typography from the platform itself. Destination reachability was not inferred or fabricated.
- This was an engineering run with `R=1`; production GEO baselines still require `R>=3` and must report the denominator per platform/query.
