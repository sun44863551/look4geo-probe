---
name: look4geo-probe
description: Use when the user asks for GEO measurement, AI visibility, brand mentions, citations, or answer comparison across domestic and international AI products.
description_zh: 用于国内外 AI 平台的 GEO 测量、品牌提及、引用和回答比较。
description_en: Use for GEO measurement, brand mentions, citations, and answer comparison across AI products.
version: 0.1.0
author: Look4GEO
---

# Look4GEO Probe

Use the private local CLI at:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local`

1. Run `/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode auto --json` unless the user requests named platforms or a comprehensive comparison.
2. For explicit selection, add one or more `--platform <name>` options and use `--mode manual`, for example `/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --json`.
3. Tell the user which platforms were selected and why.
4. The local CLI waits for completion and returns the result. Use the same absolute script path with `platforms --json` to inspect availability.
5. For a login requirement, use the same absolute script path with `login <platform> --json` and let the user complete the visible browser step. Never ask for credentials or extract browser secrets.
6. Keep successful platform results when another platform fails and state the failure clearly.

Do not upload, publish, or install this project through WorkBuddy's public Experts, Skills, or Connectors catalog. The code, browser credentials, job database, and results must remain on this Mac.
