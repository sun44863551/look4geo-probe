# Gemini Camoufox Adapter Design

Date: 2026-09-27
Status: Proposed for user review

## Purpose

Add a lightweight, machine-local Camoufox browser adapter for Gemini while preserving Look4GEO Probe as one private tool callable by both Codex and WorkBuddy. The adapter exists to improve reliability where Google rejects the current Chromium automation path. It does not promise to bypass every Google challenge and will never treat a challenge page as a successful answer.

## Scope

This change applies only to Gemini. The other seven platforms continue to use their existing adapters and routing. The project will not import the complete OneGlanse application, start a browser server, add Docker, or add PostgreSQL, ClickHouse, or Redis.

Success means:

- Codex and WorkBuddy continue to invoke the existing `probe` CLI or MCP tools without a new public command surface.
- A visible Camoufox window can be opened for the user to complete Gemini login or a Google challenge.
- Login state persists only in an ignored, project-local directory.
- A Gemini probe can send the requested question, extract the main answer, capture visible cited and surfaced sources, and return the existing `PlatformAttempt` schema.
- A real Gemini R=4 acceptance run completes four successful samples after login, with no landing greeting, challenge page, or stale answer counted as success.

## Architecture

### Adapter boundary

Create `CamoufoxGeminiAdapter`, implementing the existing `ProbeAdapter` interface:

- `health("gemini")` verifies that the Python package and downloaded Camoufox browser are available.
- `login("gemini")` launches a headed persistent context and returns `user_action_required` with concise instructions.
- `run("gemini", request)` performs one Gemini conversation and returns a `PlatformAttempt`.
- Other platform names are rejected as unsupported.

The adapter owns Camoufox lifecycle and Gemini page interaction. It reuses the existing source normalization, quality validation, failure taxonomy, artifact conventions, and result schema instead of duplicating those layers.

### Routing

Gemini uses this adapter chain:

1. `camoufox_gemini`
2. `browser_skill`
3. `ai_search_hub`

Camoufox is tried first because the observed failure occurs in the existing Chromium path. Existing fallbacks remain available during rollout. `LOGIN_REQUIRED` and `RATE_LIMITED` remain terminal for the chain so a challenge is surfaced to the user rather than hidden by a fallback result.

All other platform chains remain unchanged.

### Local storage

All Camoufox state stays under the repository's ignored `data/` tree:

```text
data/camoufox/
├── browser/             # downloaded Camoufox build/cache when configurable
├── profiles/gemini/     # persistent cookies, local storage and browser profile
└── artifacts/<job-id>/  # screenshots and diagnostic snapshots
```

No cookies, profile data, screenshots, or browser binaries are committed to Git. The adapter accepts environment overrides for deployments that need a different local path, but defaults never point to a public skill directory or cloud service.

## Browser flow

For each sample, the adapter:

1. Starts a headed persistent Camoufox context using a macOS-consistent fingerprint and the Gemini profile.
2. Opens `https://gemini.google.com/app`.
3. Detects login, consent, CAPTCHA, unusual-traffic, and rate-limit pages before entering a prompt.
4. Locates the current Gemini contenteditable composer, enters the exact safe-sent query, and verifies the live field content.
5. Activates the current send button and verifies a real state transition: composer cleared, stop control visible, or a new conversation URL/turn appears.
6. Waits only on the assistant answer region. Completion requires two consecutive stable observations and no active generation control.
7. Extracts the newest main answer, inline citations, source-panel links, and visible surfaced sources.
8. Passes the result through existing answer validity and quality checks before returning success.

Each sample starts a new conversation to prevent previous prompts or answers contaminating the measurement. Repeats reuse the persistent browser profile but not the conversation page.

## Login and user action

`probe login gemini` opens a visible Camoufox window. The user completes Google login, consent, or any challenge manually. The command does not request, store, or print account passwords.

The login command reports success only after the Gemini composer is visible. If the window closes before that point, it reports `user_action_required` rather than assuming authentication succeeded.

During a probe, challenge detection returns `FailureKind.LOGIN_REQUIRED` with a diagnostic explaining that Gemini needs manual browser verification. Automated CAPTCHA solving, cookie export from Chrome, and credential injection are out of scope.

## Failure handling

The adapter maps outcomes to the existing taxonomy:

- missing Camoufox package or browser build: adapter unhealthy;
- sign-in, consent, CAPTCHA, or unusual traffic: `login_required`;
- explicit quota or throttling message: `rate_limited`;
- composer/send transition failure: `send_failed`;
- no valid newest answer or source-area confusion: `extraction_failed`;
- bounded navigation/generation deadline: `timeout`.

A failed sample remains failed. It is never converted to “not mentioned,” and a greeting or landing-page text is never accepted as an answer.

## Dependencies and bootstrap

Camoufox is an optional project dependency, isolated from the base Python install so the seven working platforms do not depend on it. A dedicated `scripts/bootstrap-camoufox.sh` command performs the opt-in installation; the existing base bootstrap does not download a browser. The dedicated command:

- installs a pinned compatible Camoufox Python version into the project virtual environment;
- downloads a pinned stable browser build;
- sets `XDG_CACHE_HOME` to `data/camoufox/cache` for install, fetch, health, and launch operations so the browser build and manager state remain project-local;
- performs a version/launch health check without visiting Gemini.

The bootstrap step must not use `sudo`, modify the system browser, import a Chrome profile, or install Docker.

## Configuration

The following environment variables are supported:

- `LOOK4GEO_CAMOUFOX_ENABLED`: when absent, the adapter is enabled only if healthy; `0` disables it; `1` requires it and makes doctor fail when unhealthy.
- `LOOK4GEO_CAMOUFOX_PROFILE_DIR`: overrides the Gemini persistent profile path.
- `LOOK4GEO_CAMOUFOX_BROWSER`: selects an already installed pinned browser build.
- `LOOK4GEO_CAMOUFOX_HEADLESS`: defaults to false and may be set true only after headed R=4 acceptance succeeds.

No secret values are added to tracked configuration examples. The adapter is omitted from the chain when explicitly disabled or unhealthy.

## Source extraction

The implementation adopts the proven Gemini DOM boundaries already recorded in the project and in OneGlanse research:

- newest assistant answer under the Gemini message content region;
- inline links within that answer as cited sources;
- links exposed by the current answer's source panel as surfaced sources.

Extraction remains scoped to the newest assistant turn. Navigation, related-question suggestions, previous turns, account links, and Google-owned UI links are excluded. URLs pass through the existing canonicalization and deduplication pipeline.

## Testing

Implementation follows test-driven development.

Automated tests cover:

- health behavior with missing and available Camoufox runtimes;
- Gemini-only platform enforcement;
- persistent profile and project-local path selection;
- login success and user-action-required outcomes;
- prompt verification and send-state transitions;
- stable answer completion;
- newest-turn answer and source extraction;
- challenge, throttling, timeout, send, and extraction failures;
- adapter-chain ordering and fallback behavior;
- unchanged routing for the other seven platforms;
- doctor and bootstrap checks.

Browser calls are represented by a narrow injected runtime interface in unit tests. A headed smoke test verifies browser launch separately.

Real acceptance proceeds in stages:

1. launch and manual login;
2. one harmless Gemini probe;
3. R=2 repeat check;
4. R=4 final check with answer and source auditing.

The adapter is not declared stable unless the final R=4 produces four valid successful samples. If Google presents a challenge, testing pauses for user action and resumes without recording the blocked attempt as a negative GEO sample.

## Security and privacy

- Browser profile and artifacts are local and Git-ignored.
- Logs redact cookies, authorization headers, passwords, and full browser storage state.
- The adapter does not upload results or profile data.
- Existing artifacts retain the project's local data policy.
- The design reduces automation detectability but does not attempt to evade access controls or platform restrictions.

## Rollout and rollback

The new adapter is additive. Disabling `LOOK4GEO_CAMOUFOX_ENABLED` restores the current Gemini chain without changing Codex or WorkBuddy configuration. Git tag `backup/pre-visible-source-merge-20260927` remains the repository-level rollback point for the pre-integration deployment; the implementation will also create a dedicated pre-Camoufox tag before runtime installation.

## Non-goals

- Generalizing Camoufox to all platforms in this iteration.
- Running a shared browser server or exposing a network port.
- Importing OneGlanse storage, analytics, scheduling, or UI.
- Automatic CAPTCHA solving.
- Guaranteeing that Google will never issue a future challenge.
- Replacing Promptfoo or the existing BrowserSkill integration.
