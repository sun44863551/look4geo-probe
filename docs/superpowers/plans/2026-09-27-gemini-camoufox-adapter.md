# Gemini Camoufox Adapter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a project-local Camoufox adapter that makes Gemini probes more reliable while preserving the existing private CLI/MCP interface shared by Codex and WorkBuddy.

**Architecture:** Add one Gemini-only `ProbeAdapter` backed by a narrow asynchronous Camoufox runtime interface. Route Gemini through `camoufox_gemini`, then the existing BrowserSkill and AI-Search-Hub fallbacks; reuse existing models, source normalization, quality checks, storage, and CLI/MCP surfaces. Keep the optional browser package, binary, persistent profile, and artifacts local to the repository.

**Tech Stack:** Python 3.12, Camoufox Python API, Playwright-compatible async API, Pydantic, pytest/pytest-asyncio, POSIX shell.

**Spec:** `docs/superpowers/specs/2026-09-27-gemini-camoufox-adapter-design.md`

## Global Constraints

- Camoufox supports Gemini only in this iteration; the other seven platform chains remain unchanged.
- Codex and WorkBuddy continue to use the existing `probe` CLI and MCP server without a second public tool.
- The base bootstrap does not download Camoufox; installation is opt-in through `scripts/bootstrap-camoufox.sh`.
- Camoufox Python package is pinned to `0.5.6`, browser build to `official/stable/152.0.4-beta.30`, and both must remain compatible with Python `>=3.12,<3.13` on macOS arm64.
- `XDG_CACHE_HOME` is set to `<project>/data/camoufox/cache` for Camoufox install, fetch, health, and launch operations.
- The persistent Gemini profile defaults to `<project>/data/camoufox/profiles/gemini` and remains Git-ignored.
- No `sudo`, Docker, database service, Chrome-profile import, cookie export, credential injection, CAPTCHA solving, cloud upload, or new listening port.
- A challenge, login screen, consent screen, landing greeting, stale answer, or unusual-traffic page is never a successful answer or an unmentioned GEO sample.
- `LOOK4GEO_CAMOUFOX_ENABLED` semantics are exact: absent means auto-enable when healthy, `0` disables, and `1` requires health and makes doctor fail when unhealthy.
- Headed mode is the default; headless mode is not enabled in production until headed R=4 passes.

## Review Focus

- `LOOK4GEO_CAMOUFOX_ENABLED` contains an unsupported value: doctor and runtime must report a readable configuration error, not silently choose a mode; Task 1 tests this.
- The persistent profile path exists but is a file or is not writable: health must fail before browser launch without exposing profile contents; Task 2 tests this.
- Gemini re-renders the composer or send button between lookup and action: the runtime must re-resolve the current locator and verify the exact prompt/state transition; Task 3 tests this.
- The current answer has duplicate redirect/tracking URLs in both inline citations and the source panel: output must canonicalize, deduplicate, and preserve the cited role; Task 4 tests this.
- One repeat is blocked by a challenge while other repeats could run: the blocked attempt remains `login_required`, the chain stops for that sample, and it is excluded from mention-rate denominators; Tasks 5 and 7 test this.

---

## File Structure

- Create `src/look4geo_probe/adapters/camoufox_gemini.py`: Gemini-only adapter, runtime protocol, result dataclasses, optional Camoufox runtime, login/probe interaction, and failure mapping.
- Modify `src/look4geo_probe/runtime.py`: construct the optional adapter and place it first only in the Gemini chain.
- Modify `src/look4geo_probe/doctor.py`: report Camoufox mode, package/browser/profile health, and required-vs-optional status.
- Modify `pyproject.toml`: declare one pinned `camoufox` optional dependency group.
- Create `scripts/bootstrap-camoufox.sh`: opt-in, project-local package/browser installation and health verification.
- Modify `config/platforms.yaml`: declare the Gemini primary adapter and ordered fallbacks for human-readable configuration.
- Modify `config/local.env.example`: document non-secret Camoufox environment controls.
- Modify `connectors/codex/SKILL.md`, `connectors/workbuddy/README.md`, and `connectors/workbuddy/skills/look4geo-probe/SKILL.md`: explain one-command use and manual Gemini login.
- Create `tests/test_camoufox_gemini_adapter.py`: adapter/runtime unit and interaction tests.
- Modify `tests/test_connectors.py`: routing and packaging assertions.
- Modify `tests/test_doctor.py`: environment-mode and health assertions.
- Create `docs/verification/2026-09-27-gemini-camoufox-real-check.md`: installation, login, staged real tests, job IDs, and truthful acceptance result.

### Task 1: Optional Dependency, Bootstrap, and Configuration Semantics

**Files:**
- Modify: `pyproject.toml`
- Create: `scripts/bootstrap-camoufox.sh`
- Modify: `config/local.env.example`
- Modify: `src/look4geo_probe/doctor.py`
- Modify: `tests/test_doctor.py`
- Modify: `tests/test_connectors.py`

**Interfaces:**
- Consumes: repository root resolved by `Path(__file__).resolve().parents[2]`; existing `collect_checks(project_root=None, extra_env=None)`.
- Produces: `parse_camoufox_mode(value: str | None) -> str` returning `"auto"`, `"disabled"`, or `"required"`; a `camoufox` doctor check containing `ok`, `required`, `detail`, `mode`, `profile_path`, and `cache_path`; executable `scripts/bootstrap-camoufox.sh`.

- [ ] **Step 1: Write failing configuration and packaging tests**

Add tests asserting:

```python
assert parse_camoufox_mode(None) == "auto"
assert parse_camoufox_mode("0") == "disabled"
assert parse_camoufox_mode("1") == "required"
with pytest.raises(ValueError, match="LOOK4GEO_CAMOUFOX_ENABLED"):
    parse_camoufox_mode("yes")
```

Assert `pyproject.toml` contains a pinned `camoufox` optional extra, the new script sets project-local `XDG_CACHE_HOME`, never contains `sudo`, and `config/local.env.example` documents all four variables from the spec without credentials.

- [ ] **Step 2: Run tests and verify the expected red state**

Run: `.venv/bin/pytest tests/test_doctor.py tests/test_connectors.py -q`

Expected: FAIL because the parser, optional dependency, bootstrap script, and configuration documentation do not exist.

- [ ] **Step 3: Implement the parser, optional dependency, and bootstrap contract**

Add `parse_camoufox_mode(value: str | None) -> str` to `doctor.py`. Add `[project.optional-dependencies].camoufox = ["camoufox==0.5.6"]`. Create `scripts/bootstrap-camoufox.sh` to install `-e '.[camoufox]'`, set `XDG_CACHE_HOME`, fetch `official/stable/152.0.4-beta.30`, select that exact installed build, and run `python -m camoufox version`. Do not run the script in this task.

- [ ] **Step 4: Add doctor check construction**

Add `collect_camoufox_check(root: Path, env: Mapping[str, str]) -> dict`. Disabled mode is healthy and optional with detail `disabled`; auto mode is optional and reports actual package/browser health; required mode is required and reports the same health. Invalid mode produces one unhealthy required check with a readable diagnostic.

- [ ] **Step 5: Run focused and full tests**

Run: `.venv/bin/pytest tests/test_doctor.py tests/test_connectors.py -q`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml scripts/bootstrap-camoufox.sh config/local.env.example src/look4geo_probe/doctor.py tests/test_doctor.py tests/test_connectors.py
git commit -m "feat: add optional project-local camoufox runtime"
```

### Task 2: Gemini Adapter Boundary and Health

**Files:**
- Create: `src/look4geo_probe/adapters/camoufox_gemini.py`
- Create: `tests/test_camoufox_gemini_adapter.py`

**Interfaces:**
- Consumes: `ProbeAdapter`, `AdapterHealth`, `ProbeRequest`, `PlatformAttempt`, and existing enums from `models.py`.
- Produces: `GeminiBrowserRuntime` protocol; immutable `GeminiBrowserResult`; `CamoufoxGeminiAdapter(runtime, profile_dir, artifact_root, timeout=120.0)` with `name = "camoufox_gemini"`.

- [ ] **Step 1: Write failing boundary tests**

Test that the adapter rejects non-Gemini platforms, delegates health for Gemini, returns unhealthy when the profile path is a file or its parent is not writable, and creates no profile or artifact files during construction.

Use these exact protocol methods:

```python
class GeminiBrowserRuntime(Protocol):
    async def health(self, profile_dir: Path) -> AdapterHealth: ...
    async def login(self, profile_dir: Path) -> dict: ...
    async def probe(
        self, profile_dir: Path, prompt: str, timeout: float, artifact_dir: Path
    ) -> GeminiBrowserResult: ...
```

- [ ] **Step 2: Run the new test file and verify red**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py -q`

Expected: collection FAIL because the module does not exist.

- [ ] **Step 3: Implement the protocol, result model, and adapter shell**

Define `GeminiBrowserResult` with `status`, `answer`, `cited_links`, `surfaced_links`, `diagnostic`, `failure`, and `artifact_paths`. Keep it independent of Playwright types so unit tests never import Camoufox.

Implement constructor, Gemini-only guard, health delegation plus local path validation, and no-op cancellation. Leave conversion/extraction behavior for Task 4.

- [ ] **Step 4: Run the focused tests**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py -q`

Expected: boundary and health tests PASS.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/adapters/camoufox_gemini.py tests/test_camoufox_gemini_adapter.py
git commit -m "feat: define gemini camoufox adapter boundary"
```

### Task 3: Camoufox Gemini Browser Interaction

**Files:**
- Modify: `src/look4geo_probe/adapters/camoufox_gemini.py`
- Modify: `tests/test_camoufox_gemini_adapter.py`

**Interfaces:**
- Consumes: `GeminiBrowserRuntime` protocol and `GeminiBrowserResult` from Task 2; optional imports from `camoufox.async_api` only inside the concrete runtime.
- Produces: `CamoufoxRuntime(root: Path, headless: bool, browser: str | None)` implementing the protocol; internal `GeminiPageDriver` with `login()` and `probe()` methods.

- [ ] **Step 1: Write failing interaction tests against a fake Playwright-compatible page**

Cover current Gemini composer selectors, exact prompt verification, locator re-resolution after a detached element, current send-button lookup, three bounded send attempts, and successful transition on composer clear, stop-control appearance, conversation URL change, or new turn count.

Add parameterized failure tests for login, consent, CAPTCHA, Google unusual traffic, explicit quota/rate limit, navigation timeout, and unchanged send state. Assert their exact `FailureKind` values.

- [ ] **Step 2: Run interaction tests and verify red**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py -q`

Expected: FAIL because `CamoufoxRuntime` and `GeminiPageDriver` do not exist.

- [ ] **Step 3: Implement optional import and browser lifecycle**

Implement `CamoufoxRuntime.health(profile_dir)`, `login(profile_dir)`, and `probe(...)`. Import Camoufox inside launch methods so the base installation still imports Look4GEO. Use a headed persistent context by default, `os="macos"`, the supplied `user_data_dir`, and the selected installed browser only when configured.

- [ ] **Step 4: Implement page-state detection, prompt entry, send verification, and stable completion**

Implement `GeminiPageDriver.login() -> dict` and `GeminiPageDriver.probe(prompt, timeout, artifact_dir) -> GeminiBrowserResult`. Completion observes only the newest assistant answer and requires two identical non-empty observations with no active generation control. Each probe opens Gemini at a new-conversation URL/state.

- [ ] **Step 5: Run focused and regression tests**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py tests/test_browser_skill_adapter.py -q`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/look4geo_probe/adapters/camoufox_gemini.py tests/test_camoufox_gemini_adapter.py
git commit -m "feat: automate gemini with camoufox"
```

### Task 4: Answer and Source Conversion

**Files:**
- Modify: `src/look4geo_probe/adapters/camoufox_gemini.py`
- Modify: `tests/test_camoufox_gemini_adapter.py`

**Interfaces:**
- Consumes: `normalize_source_url`, `merge_sources`, and `citations_from_sources` from `sources.py`; `result_is_clean_success` from `validity.py`.
- Produces: `extract_gemini_answer(page) -> str`; `extract_gemini_sources(page) -> list[SourceRecord]`; complete `CamoufoxGeminiAdapter.run(...) -> PlatformAttempt`.

- [ ] **Step 1: Write failing extraction and conversion tests**

Use fixture-like fake DOM results to assert newest-turn-only extraction, inline links as `CITED`/`ANSWER_DOM`, source-panel links as `SURFACED`/`SOURCE_PANEL`, Google UI domain exclusion, redirect unwrapping, tracking removal, duplicate merging with cited-role precedence, and correct `SourceCaptureStatus`.

Add tests rejecting Gemini landing greetings, stale previous-turn answers, challenge text, and empty newest turns.

- [ ] **Step 2: Run focused tests and verify red**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py -q`

Expected: FAIL because extraction and adapter conversion are incomplete.

- [ ] **Step 3: Implement extraction and `PlatformAttempt` conversion**

Scope selectors to the newest assistant turn. Normalize and merge all links through existing source helpers. Set `citations`, `sources`, `source_capture_status`, `source_capture_diagnostic`, query audit fields, timestamps, and artifact paths without logging browser storage or cookies.

- [ ] **Step 4: Run focused and full tests**

Run: `.venv/bin/pytest tests/test_camoufox_gemini_adapter.py tests/test_sources.py tests/test_validity.py tests/test_quality.py -q`

Expected: PASS.

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/adapters/camoufox_gemini.py tests/test_camoufox_gemini_adapter.py
git commit -m "feat: extract gemini answers and sources via camoufox"
```

### Task 5: Runtime Routing and Shared Login Contract

**Files:**
- Modify: `src/look4geo_probe/runtime.py`
- Modify: `config/platforms.yaml`
- Modify: `tests/test_connectors.py`
- Modify: `tests/test_adapters.py`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `CamoufoxGeminiAdapter` from Tasks 2-4 and `parse_camoufox_mode` from Task 1.
- Produces: `build_adapters()` Gemini chain ordered `camoufox_gemini`, `browser_skill`, `ai_search_hub` when enabled and healthy-by-policy; unchanged public CLI/MCP schemas.

- [ ] **Step 1: Write failing routing tests**

Assert default and WorkBuddy runtimes have identical policy; Gemini includes Camoufox first in auto/required mode; disabled mode restores `browser_skill`, `ai_search_hub`; every non-Gemini chain exactly matches its pre-change order; `LOGIN_REQUIRED` from Camoufox stops the chain; ordinary send/extraction/timeout failures fall through.

- [ ] **Step 2: Run routing/interface tests and verify red**

Run: `.venv/bin/pytest tests/test_connectors.py tests/test_adapters.py tests/test_cli.py tests/test_mcp.py -q`

Expected: FAIL because runtime does not construct or route Camoufox.

- [ ] **Step 3: Implement runtime construction and configuration**

Construct project-local profile, cache, and artifact paths from the repository root. Do not instantiate or import the optional Camoufox package during service construction. Update only Gemini's chain and human-readable platform metadata.

- [ ] **Step 4: Verify CLI/MCP compatibility and failure behavior**

Run: `.venv/bin/pytest tests/test_connectors.py tests/test_adapters.py tests/test_cli.py tests/test_mcp.py tests/test_service.py -q`

Expected: PASS with unchanged command and MCP tool schemas.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/runtime.py config/platforms.yaml tests/test_connectors.py tests/test_adapters.py tests/test_cli.py tests/test_mcp.py
git commit -m "feat: route gemini through camoufox first"
```

### Task 6: Local Usage Documentation and Full Automated Verification

**Files:**
- Modify: `connectors/codex/SKILL.md`
- Modify: `connectors/workbuddy/README.md`
- Modify: `connectors/workbuddy/skills/look4geo-probe/SKILL.md`
- Modify: `tests/test_connectors.py`

**Interfaces:**
- Consumes: unchanged `scripts/probe-local login gemini` and `scripts/probe-local run ... --platform gemini` commands.
- Produces: consistent local instructions for Codex and WorkBuddy, including manual login, local profile location, challenge handling, and fallback diagnostics.

- [ ] **Step 1: Write failing documentation contract tests**

Assert both connector skills name `camoufox_gemini`, use the same login and manual-run commands, say profile data is machine-local and must not be uploaded, and prohibit counting `login_required` as “not mentioned.”

- [ ] **Step 2: Run the contract test and verify red**

Run: `.venv/bin/pytest tests/test_connectors.py -q`

Expected: FAIL because the connector documentation lacks Camoufox guidance.

- [ ] **Step 3: Update the three local connector documents**

Document the one-tool behavior without exposing internal commands as a second user-facing product. Include the manual login window expectation and truthful failure accounting.

- [ ] **Step 4: Run all automated verification**

Run: `.venv/bin/python -m pytest -q`

Expected: all tests pass.

Run: `git diff --check`

Expected: no output and exit code 0.

- [ ] **Step 5: Create pre-install rollback tag and commit documentation**

```bash
git tag backup/pre-camoufox-install-20260927
git add connectors/codex/SKILL.md connectors/workbuddy/README.md connectors/workbuddy/skills/look4geo-probe/SKILL.md tests/test_connectors.py
git commit -m "docs: document local gemini camoufox workflow"
```

### Task 7: Install, Login, and Staged Real Acceptance

**Files:**
- Create: `docs/verification/2026-09-27-gemini-camoufox-real-check.md`
- Runtime-only ignored paths: `data/camoufox/**`, `data/runs/**`

**Interfaces:**
- Consumes: `scripts/bootstrap-camoufox.sh`; `scripts/probe-local doctor`; `scripts/probe-local login gemini`; existing `run` command with manual Gemini routing.
- Produces: installed project-local runtime, persistent user-completed login, R=1/R=2/R=4 evidence, and an honest stable/intermittent/blocked conclusion.

- [ ] **Step 1: Verify rollback and clean source state before installation**

Run: `git status --short && git rev-parse backup/pre-camoufox-install-20260927`

Expected: no source changes and a valid tag commit.

- [ ] **Step 2: Run the opt-in installer with approved network access**

Run: `scripts/bootstrap-camoufox.sh`

Expected: pinned Python package and browser build install under project-controlled virtual environment/cache; version/health command exits 0. Record exact versions and disk usage.

- [ ] **Step 3: Run doctor**

Run: `LOOK4GEO_CAMOUFOX_ENABLED=1 scripts/probe-local doctor --json`

Expected: required Camoufox check is healthy, profile/cache paths are project-local, and all other required checks remain healthy.

- [ ] **Step 4: Open headed login and wait for user completion**

Run: `LOOK4GEO_CAMOUFOX_ENABLED=1 scripts/probe-local login gemini --json`

Expected: a visible Camoufox window opens; after the user completes Google login/challenge, the command confirms the Gemini composer. Never collect credentials in logs.

- [ ] **Step 5: Run one harmless probe**

Run: `LOOK4GEO_CAMOUFOX_ENABLED=1 scripts/probe-local run "Reply exactly: Look4GEO Gemini Camoufox OK" --mode manual --platform gemini --repeats 1 --json`

Expected: one successful `camoufox_gemini` attempt whose answer contains the requested phrase; no landing greeting or challenge text.

- [ ] **Step 6: Run R=2 and inspect answer/source boundaries**

Run a neutral web-source question with `--repeats 2 --json` and store its job ID. Expected: two independent new conversations, two main answers, and source fields that truthfully distinguish `captured`, `none_exposed`, or `failed`.

- [ ] **Step 7: Run final R=4**

Run the agreed Gemini acceptance question with `--repeats 4 --json`. Expected: four valid successful samples from `camoufox_gemini`. A challenge or failed sample means acceptance is not passed; do not replace it with a fallback success for the stability verdict.

- [ ] **Step 8: Verify blocked samples are excluded from metrics**

Inspect the result JSON and any generated aggregate input. Assert every non-success has a failure/status value and is not encoded as mention `0` or citation `0`.

- [ ] **Step 9: Write and commit the real-check report**

Record commands, exact versions, job IDs, per-sample adapter/status/source status, diagnostics without secrets, and one conclusion: stable only for 4/4, intermittent for partial success, or blocked for 0/4/user action.

```bash
git add docs/verification/2026-09-27-gemini-camoufox-real-check.md
git commit -m "test: verify gemini camoufox real browser flow"
```

- [ ] **Step 10: Final verification and Git backup**

Run: `.venv/bin/python -m pytest -q && git diff --check && git status --short`

Expected: all tests pass, no diff errors, and no uncommitted source files. Push the implementation branch and its rollback tag; do not commit anything under `data/`.
