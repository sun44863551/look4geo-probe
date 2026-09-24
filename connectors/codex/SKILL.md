---
name: look4geo-probe
description: Use when measuring GEO visibility or comparing answers, citations, and brand mentions across Chinese and international AI products.
---

# Look4GEO Probe

Use the `look4geo-probe` MCP tools for real-product GEO measurements.

- Default to `probe_run` with `mode: auto`; use `compare` for cross-market comparison, `all` only when explicitly requested, and `manual` when platforms are named.
- Report the selected platforms and routing reasons.
- `probe_run` returns a job ID. Poll `probe_status`, then call `probe_result` only after a terminal state.
- If the state is `waiting_for_login`, call `probe_login` and ask the user to complete login in the visible browser. Never request passwords, cookies, or tokens.
- Preserve partial results and identify failed platforms rather than discarding successful answers.

