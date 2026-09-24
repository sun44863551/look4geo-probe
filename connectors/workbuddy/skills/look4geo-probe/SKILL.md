---
name: look4geo-probe
description: Use when the user asks for GEO measurement, AI visibility, brand mentions, citations, or answer comparison across domestic and international AI products.
description_zh: 用于国内外 AI 平台的 GEO 测量、品牌提及、引用和回答比较。
description_en: Use for GEO measurement, brand mentions, citations, and answer comparison across AI products.
version: 0.1.0
author: Look4GEO
---

# Look4GEO Probe

Call the installed `look4geo-probe` MCP server.

1. Use `probe_run` with `auto` unless the user requests named platforms or a comprehensive comparison.
2. Tell the user which platforms were selected and why.
3. The initial call returns a job ID. Poll `probe_status`; do not hold one MCP request open waiting for browser answers.
4. Fetch `probe_result` after `succeeded`, `partial`, `failed`, or `waiting_for_login`.
5. For `waiting_for_login`, call `probe_login` and let the user complete the visible browser step. Never ask for credentials or extract browser secrets.
6. Keep successful platform results when another platform fails and state the failure clearly.

