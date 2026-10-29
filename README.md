# Look4GEO Probe

Look4GEO Probe is a private, machine-local web research probe for measuring how AI assistants discover, mention, recommend, and cite companies, products, and other entities.

It is designed for GEO (Generative Engine Optimization) baseline research. The probe sends questions through real, logged-in browser sessions, captures the answer shown to the user, and records browser-visible sources. It does not replace web products with model APIs and does not infer hidden retrieval results.

## Why this project exists

Traditional web analytics cannot show whether an AI assistant recommends a supplier for a buyer's question. Look4GEO Probe turns that question into an auditable observation:

- the original question and buyer-language baseline;
- the selected platform and adapter;
- the raw answer and normalized answer;
- visible cited and surfaced sources;
- entity mentions, candidate-set presence, recommendation signals, and citations;
- explicit outcomes for success, login required, rate limiting, send failure, extraction failure, and timeout.

Failed or incomplete observations are never silently converted into “not mentioned” results.

## Supported web platforms

Platform selection is explicit and remains under the caller's control. The current local routing supports:

- Doubao
- DeepSeek
- Yuanbao
- Baidu AI
- ChatGPT
- Gemini
- Perplexity
- Grok

Qwen is not an active platform in the current unified configuration. Gemini uses Flash-oriented web sampling in this project; API-based model answers are outside the probe's measurement path.

## Architecture

The project exposes one local service through two compatible entry points:

```text
Codex / WorkBuddy
        |
        +--> local CLI or local MCP server
                    |
                    +--> routing and health checks
                    +--> platform adapters
                    +--> local SQLite run store
                    +--> structured answer/source evidence
```

The main components are:

- `src/look4geo_probe/` — the core Python package, service, router, adapters, models, and storage;
- `scripts/probe-local` — the repository-local launcher used by Codex and WorkBuddy;
- `connectors/workbuddy/` — private local WorkBuddy integration notes and configuration;
- `vendor/AI-Search-Hub/` — a pinned open-source browser adapter source tree used where appropriate;
- `data/` — machine-local runtime data and browser artifacts; login state and results must remain local;
- `tests/` — unit and regression tests for routing, adapters, failures, source capture, and rate-limit handling.

## Browser-based collection

Look4GEO Probe uses logged-in browser sessions rather than API credentials for web sampling. Each platform can be assigned its own browser profile and BrowserSkill instance.

ChatGPT sampling is bound to the dedicated `Look4GEO Sampling` browser profile. The configured BrowserSkill instance must be supplied through private local configuration. If that instance is unavailable, the probe fails explicitly; it never falls back to the user's daily browser.

Browser-profile isolation separates cookies, history, login state, and account sessions. It does not separate the network address or remove platform-level usage limits. The project does not attempt CAPTCHA bypass, stealth automation, credential extraction, or anti-bot circumvention.

## Installation

The project currently targets Python 3.12.

```bash
git clone https://github.com/sun44863551/look4geo-probe.git
cd look4geo-probe

python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Optional Camoufox support for the isolated Gemini route:

```bash
python -m pip install -e '.[dev,camoufox]'
```

Keep local settings in the ignored `config/local.env`. Never commit browser profiles, cookies, tokens, customer datasets, or raw private run artifacts.

## Command-line usage

Check available platforms and their local health state:

```bash
scripts/probe-local platforms --json
```

Run one question with explicitly selected platforms:

```bash
scripts/probe-local run \
  "Which suppliers are commonly recommended for DCTA monohydrate?" \
  --mode manual \
  --platform chatgpt \
  --platform gemini \
  --platform perplexity \
  --json
```

Use automatic routing when the request should be matched to healthy platforms:

```bash
scripts/probe-local run \
  "Recommend suppliers for a Japanese buyer evaluating DCTA monohydrate." \
  --mode auto \
  --json
```

For an explicit buyer-language baseline:

```bash
scripts/probe-local run \
  "DCTA monohydrate suppliers for a Japanese buyer" \
  --mode manual \
  --platform chatgpt \
  --buyer-baseline japan \
  --json
```

The default buyer baseline is English. Japanese and Korean baselines must be selected explicitly with `japan` or `korea`.

## MCP and WorkBuddy

Codex and WorkBuddy are intended to call the same local implementation. The project is not intended to be imported into a public WorkBuddy skills or connector marketplace.

WorkBuddy should use the repository-local `scripts/probe-local` launcher or the optional local stdio MCP configuration. Platform choice remains explicit through repeated `--platform` flags or the equivalent MCP request fields.

Typical MCP operations include:

- discovering platform health and login state;
- running a probe with a selected platform set;
- checking a job status;
- requesting user-assisted login instructions.

## Evidence and failure semantics

The probe records browser-observable evidence only. A source may be classified as `cited` when linked from the answer or `surfaced` when shown in the platform's visible source panel without being linked from the answer body.

Schema 4 separates link purpose (`source_kind`) from evidence location. Google
shopping/search entries and opaque Google-hosted links remain in `sources` as
`surfaced`, but are excluded from `citations`. Publisher documentation such as
`developers.google.com` is not excluded. `website` describes a link type, not
publisher credibility or independently verified citation intent.

Each attempt exposes `source_statistics`: `total`, `website_cited`,
`website_surfaced`, `shopping_entries`, `search_entries`, and
`hosted_unresolved`. Counts describe saved evidence; when no evidence was
captured, statistics are `null`, not a measured absence. Failed or partial
captures must still be distinguished using status and source-capture fields.
Report publisher visibility from both website categories, while keeping the
cited/surfaced distinction. Do not use `len(citations)` as total source coverage.

Recount existing batch records without new web requests or overwriting inputs:

```bash
./scripts/probe-local source-report /absolute/path/to/raw \
  --platform gemini --output /absolute/path/to/new-source-report.json
```

The output file must not already exist and its parent directory must exist.
The report includes all links, separate counts, publisher-host statistics,
per-question coverage, and hashes of the original input files. Saved schemas
1–3 remain readable; historical files and reports are not rewritten implicitly.
Existing custom report scripts must adopt the separate categories rather than
filtering all source evidence to `source_role == "cited"`.

The following states remain distinct:

- successful answer with visible sources;
- successful answer with no exposed sources;
- login required;
- rate limited or quota suspended;
- send failure;
- extraction failure;
- timeout;
- incomplete or unstable answer.

Rate limits are handled conservatively: the affected platform is paused, failed observations remain uncounted, and the probe does not claim that a failed question omitted the target entity.

## Testing

Run the full local test suite:

```bash
.venv/bin/python -m pytest -q
```

The repository's latest verified suite contains adapter, routing, source-capture, account-isolation, rate-limit, and batch-pause regressions. Real web checks require the relevant browser profile to be connected and are intentionally separate from the offline test suite.

## Privacy and operational boundaries

This is a local research tool. Results, SQLite state, browser profiles, and login sessions stay on the machine unless the operator explicitly exports them. Do not commit private sampling output or credentials.

The probe is a measurement instrument, not a guarantee of platform behavior. Platform UI changes, account restrictions, browser-extension state, quotas, and network conditions can make an observation unavailable. Such cases are reported as operational failures and must be resolved or rerun before being used in a baseline.

## Project status

The V1 foundation is implemented and backed up in Git. Browser adapters and platform availability are operationally dependent on the current web UI, connected browser profiles, account state, and platform limits. WorkBuddy invocation and long-running ChatGPT collection require additional real-environment validation before being treated as production-stable.

## License and upstream components

This repository contains locally integrated or pinned open-source components. Review the individual upstream licenses in their respective directories before redistribution. The repository's local integration code and documentation are maintained for private Look4GEO Probe operation.
