---
name: look4geo-probe
description: Use when measuring GEO visibility or comparing answers, citations, and brand mentions across Chinese and international AI products.
---

# Look4GEO Probe

Use this repository's private local CLI for real-product GEO measurements:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local`

For one named platform, run:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --json`

For several named platforms, repeat `--platform`, for example:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --platform perplexity --json`

The optional local `look4geo-probe` MCP tools expose the same service and result schema.

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

This is a local-only skill. Do not publish or install it in a public skill or connector catalog.
