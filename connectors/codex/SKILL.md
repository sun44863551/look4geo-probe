---
name: look4geo-probe
description: Use when measuring GEO visibility or comparing answers, citations, and brand mentions across Chinese and international AI products.
---

# Look4GEO Probe

买家语言基线：日本买家传 `--buyer-baseline japan`，韩国买家传 `korea`；没有明确要求则默认 `english`，不要仅凭问题语种推断。MCP 使用 `buyer_baseline`。保留原问题，每条批量请求均传参数，检查点区分基线。当前仅 ChatGPT BrowserSkill 校验语言；仅 `locale_verified=true` 为已验证，null 不得当作成功基线。语言不代表地理位置。

Use this repository's private local CLI for real-product GEO measurements:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local`

For one named platform, run:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --json`

For long research answers, set the per-adapter budget explicitly with `--timeout 600`. Keep the outer task timeout higher than this value so fallback adapters can finish.

When the expected entity is known, add one or more quality aliases:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform doubao --expected-term DCTA --expected-term 环己二胺四乙酸 --json`

For several named platforms, repeat `--platform`, for example:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --platform perplexity --json`

The optional local `look4geo-probe` MCP tools expose the same service and result schema.

Active platform IDs are `doubao`, `deepseek`, `yuanbao`, `baidu`, `chatgpt`,
`gemini`, `perplexity`, and `grok`. Baidu uses the same unified CLI/MCP tool;
there is no separate Baidu tool. Qwen is not an active platform.

ChatGPT requires `LOOK4GEO_CHATGPT_BROWSER_ID` from the isolated probe Chrome
profile. It may equal `LOOK4GEO_BROWSER_ID` when that is the same probe profile.
If unavailable, suspend ChatGPT; never select the daily browser. See
`docs/chatgpt-account-isolation.md`.

Gemini remains part of this same tool. Its preferred local adapter is
`camoufox_gemini`; BrowserSkill and AI-Search-Hub remain fallbacks. Before the
first Gemini measurement, open the visible machine-local login window with:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local login gemini --json`

After login, use the ordinary unified command:

`/Users/x/Documents/修复/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform gemini --json`

The persistent profile under `data/camoufox/profiles/gemini` is machine-local
and must not be uploaded. If Google shows a challenge, let the user complete it
in the visible window; never request or record credentials. Report the selected
adapter and fallback diagnostic. A `login_required` result is an uncollected
sample, not "not mentioned", and must be excluded from mention/citation rates.

- Default to `probe_run` with `mode: auto`; use `compare` for cross-market comparison, `all` only when explicitly requested, and `manual` when platforms are named.
- Report the selected platforms and routing reasons.
- `probe_run` returns a job ID. Poll `probe_status`, then call `probe_result` only after a terminal state.
- If the state is `waiting_for_login`, call `probe_login` and ask the user to complete login in the visible browser. Never request passwords, cookies, or tokens.
- Preserve partial results and identify failed platforms rather than discarding successful answers.

## Source truth boundary

- `sources` contains only source links visibly exposed by the product UI for this answer. It is not a list of every source the model may have used internally.
- `cited` means the source was linked from the selected answer; `surfaced` means the UI showed it in a source/results panel but the answer did not link it.
- `source_capture_status: captured` means at least one visible source was collected; `none_exposed` means the UI exposed none.
- `source_capture_status: failed` means source extraction failed. Read `source_capture_diagnostic`; do not turn this into "no sources" and do not change a successful answer `status` to failed.
- Keep legacy `citations` in reports alongside the richer `sources` field.
- Treat answer `status` as transport/extraction status. Report `quality_status` separately: `passed` means an expected alias matched, `review_required` means a structural warning such as a non-ASCII URL hyphen, and `failed` means configured expected terms were absent. Never count `quality_status: failed` as a trustworthy business answer.

This is a local-only skill. Do not publish or install it in a public skill or connector catalog.

## Verified operating notes for this machine

Confirmed by a real 8-platform run (2026-10-02). Follow these or you will
waste cycles on false failures.

- Always invoke `scripts/probe-local`, never `.venv/bin/probe` directly. The
  wrapper sources `config/local.env`; bypassing it crashes with
  `ValueError: browser_instance_id is required`.
- macOS has no `timeout` command. Do not wrap probes in `timeout N …`.
- The browser adapters fail transiently. `ERR_CONNECTION_CLOSED` and
  `browser_skill: timeout` on the first attempt usually succeed on an immediate
  retry (observed on grok, baidu and perplexity in the same run). Retry a failed
  platform once before recording it as a failure.
- Give chat-style long answers room: `--timeout 300` or more. Perplexity
  returned 3463 chars once allowed past the default budget.
- A sandboxed shell's own `curl`/`lsof` results are NOT trustworthy for
  reachability — they traverse a sandbox proxy and cannot see root-owned
  listener processes. Judge reachability with `bsk navigate <url> --session <id>
  --json` and read `final_url`; the browser runs outside that sandbox.
- Platforms needing a human login before they yield samples: `deepseek` and
  `doubao` (log in inside the visible Chrome window), and `gemini`
  (`probe-local login gemini`). Until then they return `waiting_for_login`.
- `doubao` may first return `doubao-region-ban` when the proxy egress is
  overseas. Completing login cleared it in practice — retry after login before
  changing any proxy rules.
- `chat.baidu.com` now 302-redirects to `wenxin.baidu.com`; its first probe
  times out more often than the others.
