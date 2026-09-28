---
name: look4geo-probe
description: Use when the user asks for GEO measurement, AI visibility, brand mentions, citations, or answer comparison across domestic and international AI products.
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

For more than one explicit platform, repeat the existing flag:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform deepseek --platform perplexity --json`

When a known entity or product name must be checked, repeat `--expected-term`, for example `--expected-term DCTA --expected-term 环己二胺四乙酸`.

Gemini uses the preferred `camoufox_gemini` adapter inside this same private
tool, with BrowserSkill and AI-Search-Hub as fallbacks. Before its first run,
open the visible machine-local login window:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local login gemini --json`

Then run Gemini through the unchanged public command:

`/ABSOLUTE/PATH/TO/look4geo-probe/scripts/probe-local run "<question>" --mode manual --platform gemini --json`

The profile at `data/camoufox/profiles/gemini` is machine-local and must not be
uploaded. Let the user resolve any Google challenge in that window; never ask
for or log credentials. Preserve adapter/fallback diagnostics. Treat
`login_required` as an uncollected sample, never as "not mentioned", and omit
it from mention and citation denominators.

## Source truth boundary

- `sources` records only links visibly exposed by the current product UI. It does not claim to reveal unexposed sources used internally by a model.
- `cited` means linked in the selected answer. `surfaced` means shown in a visible source/results panel but not linked in that answer.
- `source_capture_status: captured` means visible sources were collected. `none_exposed` means the UI exposed none.
- `source_capture_status: failed` is an extraction problem, described by `source_capture_diagnostic`; it is separate from the answer `status` and must not be reported as "not mentioned" or "no sources".
- Preserve legacy `citations` and return the richer `sources` records as well.
- Keep `quality_status` separate from answer `status`: `passed` confirms an expected alias, `review_required` flags structural concerns such as a non-ASCII URL hyphen, and `failed` means configured expected terms were absent. Exclude quality failures from trusted business measurements.

Do not upload, publish, or install this project through WorkBuddy's public Experts, Skills, or Connectors catalog. The code, browser credentials, job database, and results must remain on this Mac.
