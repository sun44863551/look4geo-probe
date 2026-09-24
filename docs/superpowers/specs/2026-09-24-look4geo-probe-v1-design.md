# Look4GEO Probe V1 Design

## 1. Goal

Deploy a local-first GEO probe on the user's Apple M1 Mac mini that can ask real AI products and API-backed models, preserve the answers and evidence, and be invoked consistently by Codex, WorkBuddy, or a terminal command.

The system must autonomously choose suitable platforms by default while still supporting explicit platform selection. V1 prioritizes reuse of maintained open-source projects and avoids a mandatory Docker dependency.

## 2. Success Criteria

- Codex and WorkBuddy can call the same probe through MCP.
- A terminal user can run the same operations through one `probe` CLI.
- `auto` mode chooses one to three suitable platforms and explains the choice.
- `compare`, `all`, and `manual` modes are available for deterministic control.
- The target platform set is Doubao, DeepSeek, Yuanbao, Qwen, ChatGPT, Gemini, Perplexity, and Grok.
- Each completed run stores the prompt, routing decision, raw answer, citations when available, timestamps, status, and diagnostic evidence.
- Long browser jobs do not exceed WorkBuddy's recommended MCP response window: submission returns a job ID, and status/result calls are separate.
- Login credentials, cookies, and browser profiles remain on the Mac mini and are never copied into result files.
- A minimal test proves one domestic browser platform, one overseas browser platform, MCP discovery, and the Promptfoo evaluation path.

## 3. Scope

### Included in V1

- Local orchestration service and SQLite metadata/result store.
- MCP server using local `stdio` transport for Codex and WorkBuddy.
- CLI wrapper over the same application service.
- Autonomous routing with documented, deterministic rules.
- AI-Search-Hub adapters for its supported sites.
- BrowserSkill-backed fallback adapters for missing or broken UI integrations.
- Promptfoo integration for API-based batch evaluation.
- Human-in-the-loop login, CAPTCHA, and verification handling.
- Structured JSON results plus human-readable Markdown exports.

### Deferred to V1.1

- Full OneGlanse deployment with Docker, PostgreSQL, ClickHouse, and dashboard.
- Remote/public MCP hosting and multi-user authentication.
- Background schedules and recurring monitoring.
- Automatic scoring by a paid judge model.
- A custom web dashboard.

OneGlanse is used as a reference for GEO metrics and provider behavior in V1, not as a runtime dependency.

## 4. Chosen Architecture

```text
Codex Skill ───────┐
                   ├── local stdio MCP ── Look4GEO Probe Core
WorkBuddy Skill ───┘                         │
                                             ├── Router
probe CLI ───────────────────────────────────┤
                                             ├── Job Manager
                                             ├── Adapter Registry
                                             │   ├── AI-Search-Hub
                                             │   ├── BrowserSkill fallback
                                             │   └── Promptfoo
                                             └── SQLite + JSON/Markdown artifacts
```

The MCP server and CLI are thin interfaces. They call the same Python application service so routing, validation, storage, and error handling cannot diverge between clients.

Python 3.12 is the orchestration runtime because AI-Search-Hub is Python-based. Node.js 22 LTS is installed for Promptfoo. BrowserSkill is installed from its published Apple Silicon CLI and paired with its Chrome extension. Google Chrome is reused as the visible, logged-in browser.

## 5. Directory Layout

```text
look4geo-probe/
├── apps/
│   ├── cli/                    # `probe` command entry point
│   └── mcp/                    # stdio MCP server
├── src/look4geo_probe/
│   ├── service.py              # shared application service
│   ├── router.py               # autonomous routing
│   ├── jobs.py                 # async process/job lifecycle
│   ├── models.py               # validated request/result schemas
│   ├── storage.py              # SQLite and artifact persistence
│   └── adapters/
│       ├── base.py             # adapter contract
│       ├── ai_search_hub.py
│       ├── browser_skill.py
│       └── promptfoo.py
├── vendor/
│   └── AI-Search-Hub/          # pinned upstream checkout
├── config/
│   ├── platforms.yaml          # capabilities and adapter preferences
│   └── routing.yaml            # routing rules and limits
├── data/
│   ├── probe.sqlite3           # job and result metadata
│   └── runs/<job-id>/          # raw/normalized outputs and evidence
├── connectors/
│   ├── codex/                  # Codex-facing Skill/config examples
│   └── workbuddy/              # connector metadata, MCP config, Skill
├── evals/
│   └── promptfoo/              # API evaluation configuration
├── scripts/                    # setup, doctor, and smoke-test helpers
└── tests/                      # router, storage, adapter, MCP, CLI tests
```

Browser profiles and credentials are stored only in their owning Chrome/BrowserSkill locations, outside `data/` and version control.

## 6. Public Interface

### MCP tools

- `probe_run(prompt, mode="auto", platforms=[], options={}) -> {job_id, selected_platforms, reasons, status}`
- `probe_status(job_id) -> {status, per_platform, started_at, updated_at}`
- `probe_result(job_id, format="structured") -> {routing, responses, citations, errors, artifacts}`
- `probe_platforms() -> {platforms, capabilities, adapter, login_state, availability}`
- `probe_login(platform) -> {status, instructions}`

`probe_run` performs validation and routing, persists the job, starts worker processes, and returns without waiting for full AI responses.

### CLI

```text
probe run "question" --mode auto
probe run "question" --mode manual --platform doubao --platform chatgpt
probe status <job-id>
probe result <job-id> --format markdown
probe platforms
probe login perplexity
probe doctor
```

Exit codes distinguish success, partial platform failure, authentication required, invalid input, and internal failure.

## 7. Autonomous Routing

The router uses configuration rather than hard-coded client behavior. Each platform advertises:

- geographic and language strengths;
- source ecosystem strengths;
- citation capability;
- real-time/search capability;
- browser or API execution type;
- login and health state;
- concurrency and cooldown limits.

Modes behave as follows:

- `auto`: choose one to three healthy platforms that best cover the request.
- `compare`: choose a balanced domestic/international set, normally two to five platforms.
- `all`: select every enabled and healthy platform.
- `manual`: use only the requested platforms; unavailable platforms produce explicit per-platform errors rather than silent substitution.

Initial intent mapping:

- Chinese consumer trends and ByteDance ecosystems: Doubao.
- WeChat and Tencent ecosystems: Yuanbao.
- Alibaba, commerce, and general Chinese web: Qwen.
- Chinese technical reasoning: DeepSeek.
- General global brand recommendations: ChatGPT.
- Google and global web discovery: Gemini.
- Citation-heavy research: Perplexity.
- X and real-time social discussion: Grok.

For mixed domestic/international GEO questions, `auto` must include at least one domestic and one international platform when both are healthy. The saved routing record includes matched rules, excluded platforms, health/login constraints, and fallback decisions.

## 8. Adapter Strategy

### AI-Search-Hub

This is the preferred adapter for Doubao, Yuanbao, Qwen, Gemini, and Grok because it already provides platform-specific Playwright scripts and a unified launcher. The repository is pinned to a tested commit. Upstream code is not modified directly; compatibility patches, if required, are maintained in the Look4GEO adapter layer.

### BrowserSkill

BrowserSkill is the preferred fallback and the V1 path for DeepSeek, ChatGPT, and Perplexity. It uses the user's visible, logged-in Chrome browser and supports both Codex and WorkBuddy. Each browser task must operate in an Agent Window or explicitly borrowed tab, record non-secret evidence, and always close or return its session.

If an AI-Search-Hub adapter fails its health check after a page change, the registry may fall back to BrowserSkill when a matching workflow exists. This fallback is recorded in the result.

### Promptfoo

Promptfoo is an API evaluation backend, not a substitute for UI probes. A custom provider can call the Probe Core for comparison runs, while built-in providers are used for direct API baselines. API keys are read from the process environment or a local secrets mechanism and are never stored in YAML or results.

## 9. Job and Data Model

Job states are `queued`, `routing`, `running`, `waiting_for_login`, `partial`, `succeeded`, `failed`, and `cancelled`.

Each platform attempt stores:

- adapter and platform identifiers;
- start/end times and duration;
- raw answer text;
- normalized answer text;
- citations with URL and visible label when available;
- browser evidence paths when enabled;
- retry/fallback history;
- a sanitized diagnostic message.

The result schema is versioned from `1`. Raw upstream output is preserved alongside normalized fields so future parser improvements do not destroy original evidence.

## 10. Login and Security

- The user completes initial login and CAPTCHA in visible Chrome windows.
- The system never reads, prints, exports, or stores passwords, cookies, session tokens, or Chrome credential databases.
- AI-Search-Hub uses an isolated debug profile seeded only through its supported flow.
- BrowserSkill owns its browser connection state and requires explicit tab borrowing.
- Logs sanitize query strings, authorization headers, cookie values, and known key patterns.
- Local MCP uses `stdio`; no listening network service is required for V1.
- Result directories use user-only filesystem permissions where supported.
- Any future remote transport requires a separate authentication and threat-model review.

## 11. Failure Handling

- Missing login moves an attempt to `waiting_for_login` and returns actionable instructions.
- CAPTCHA or verification pauses that platform without blocking completed platforms.
- Timeout produces a partial result and preserves captured evidence.
- Selector/page changes trigger one bounded retry, then an optional BrowserSkill fallback.
- Platform rate limits stop automatic retries until the configured cooldown expires.
- A failed platform never erases successful responses from the same job.
- If every selected platform is unavailable in `auto`, the router returns a structured failure explaining exclusions; it does not silently use an unrequested API substitute.
- Worker restart recovery marks orphaned `running` jobs as interrupted and keeps their artifacts.

## 12. Configuration

`platforms.yaml` defines enabled state, capabilities, adapter priority, timeout, concurrency, and login URL. `routing.yaml` defines intent signals, mode limits, domestic/international balancing, and fallback policy.

User overrides are local and preserved across upstream updates. Secrets never appear in either file.

## 13. Verification Plan

Automated tests cover:

- routing for domestic, international, mixed, citation-heavy, and real-time prompts;
- health/login exclusion and fallback selection;
- explicit manual selection without substitution;
- job state transitions and interrupted-job recovery;
- SQLite persistence and schema-version round trips;
- MCP tool schemas and immediate job-ID response;
- CLI/MCP parity;
- secret redaction.

Local smoke tests cover:

1. `probe doctor` validates Python, Node, Chrome, BrowserSkill, AI-Search-Hub, Promptfoo, database access, and required ports.
2. One AI-Search-Hub domestic request completes and stores a raw answer.
3. One BrowserSkill overseas request completes using a visible logged-in session.
4. An `auto` mixed-market prompt selects at least one domestic and one international platform and records reasons.
5. Codex discovers and invokes `probe_platforms` through MCP.
6. WorkBuddy loads the local connector and invokes `probe_platforms` through MCP.
7. Promptfoo runs a minimal direct API or mock-provider evaluation without exposing credentials.

Tests requiring paid API keys are optional and clearly separated from the no-cost browser smoke tests.

## 14. Deployment Sequence

1. Install user-scoped Python 3.12 and Node.js 22 LTS without replacing macOS system Python.
2. Create the isolated Look4GEO project environment and install pinned dependencies.
3. Install and verify BrowserSkill CLI; the user installs/enables its Chrome extension.
4. Pin AI-Search-Hub and verify its launcher against installed Chrome.
5. Install Promptfoo and run an offline/basic verification.
6. Build the Probe Core, router, adapters, storage, CLI, and MCP server.
7. Register MCP with Codex and package the WorkBuddy connector and Skill.
8. Complete user-assisted platform logins.
9. Run the automated tests and smoke-test matrix.
10. Produce operating, login, recovery, and unified-command documentation.

## 15. Operational Boundaries

UI automation depends on third-party page structure and account policies. A passing smoke test proves the current setup, not permanent compatibility. Adapter health and fallback evidence must make breakage diagnosable.

The system must respect platform rate limits and user account terms. V1 does not attempt stealth automation, CAPTCHA bypass, credential extraction, or anti-bot circumvention.
