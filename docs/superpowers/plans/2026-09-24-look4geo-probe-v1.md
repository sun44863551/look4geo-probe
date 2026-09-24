# Look4GEO Probe V1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy a local-first GEO probe that autonomously routes questions across eight AI products and exposes one shared interface to Codex, WorkBuddy, and the terminal.

**Architecture:** A Python 3.12 Probe Core owns routing, asynchronous jobs, normalized results, and SQLite persistence. Thin CLI and stdio MCP interfaces call the same service; AI-Search-Hub, BrowserSkill, and Promptfoo are isolated behind adapters so browser or upstream changes do not leak into clients.

**Tech Stack:** Python 3.12, Pydantic 2, Typer, MCP Python SDK, SQLite, PyYAML, pytest, Node.js 22 LTS, Promptfoo, Google Chrome, Tencent BrowserSkill, AI-Search-Hub.

**Spec:** `docs/superpowers/specs/2026-09-24-look4geo-probe-v1-design.md`

## Global Constraints

- Run on macOS 14.8.9, Apple M1, 16 GB RAM, with user-scoped dependencies.
- Do not replace or modify macOS system Python 3.9.6.
- Do not require Docker in V1; full OneGlanse deployment is deferred to V1.1.
- Keep credentials, cookies, API keys, and browser profiles outside result files and Git.
- Use local stdio MCP; V1 must not open a public network listener.
- `probe_run` must return a job ID without waiting for a full browser response.
- Preserve raw upstream output and store normalized schema version `1`.
- Respect platform terms, rate limits, and human verification; never bypass CAPTCHA or anti-bot controls.
- Use `auto`, `compare`, `all`, and `manual` routing modes exactly as defined in the spec.

## Review Focus

- Empty or whitespace-only prompts must fail before a job is persisted; Task 2 tests this through `ProbeRequest` validation.
- Mixed-market prompts with one side unavailable must choose the healthy side and record the missing coverage; Task 3 tests this degraded route.
- Duplicate submissions must receive distinct job IDs and artifact directories; Task 4 tests this storage invariant.
- A worker crash after persistence must be recovered as `failed`/interrupted without losing artifacts; Task 4 tests orphan recovery.
- Adapter output containing token-like strings must be redacted from diagnostics while raw answer text remains intact; Task 5 tests field-specific sanitization.

---

### Task 1: Reproducible Project Bootstrap and Doctor

**Files:**
- Create: `.gitignore`
- Create: `pyproject.toml`
- Create: `config/platforms.yaml`
- Create: `config/routing.yaml`
- Create: `scripts/bootstrap.sh`
- Create: `scripts/doctor.py`
- Create: `tests/test_doctor.py`

**Interfaces:**
- Consumes: Host tools found by the approved environment audit.
- Produces: Python package `look4geo_probe`, console script `probe`, `DoctorCheck` records, and pinned runtime configuration consumed by all later tasks.

- [ ] **Step 1: Add bootstrap/doctor tests**

```python
# tests/test_doctor.py
from look4geo_probe.doctor import summarize_checks


def test_required_failure_makes_doctor_unhealthy():
    checks = [
        {"name": "chrome", "required": True, "ok": True, "detail": "found"},
        {"name": "python", "required": True, "ok": False, "detail": "3.9"},
    ]
    assert summarize_checks(checks) == {"ok": False, "failed": ["python"]}


def test_optional_failure_does_not_make_doctor_unhealthy():
    checks = [{"name": "docker", "required": False, "ok": False, "detail": "missing"}]
    assert summarize_checks(checks) == {"ok": True, "failed": []}
```

- [ ] **Step 2: Run the test and confirm the package is absent**

Run: `python3 -m pytest tests/test_doctor.py -v`

Expected: FAIL because `look4geo_probe.doctor` does not exist.

- [ ] **Step 3: Add package metadata and dependency pins**

```toml
# pyproject.toml
[build-system]
requires = ["hatchling>=1.27,<2"]
build-backend = "hatchling.build"

[project]
name = "look4geo-probe"
version = "0.1.0"
requires-python = ">=3.12,<3.13"
dependencies = [
  "mcp>=1.13,<2",
  "pydantic>=2.11,<3",
  "PyYAML>=6.0,<7",
  "typer>=0.16,<1",
]

[project.optional-dependencies]
dev = ["pytest>=8.4,<9", "pytest-asyncio>=1.1,<2"]

[project.scripts]
probe = "look4geo_probe.cli:app"
```

- [ ] **Step 4: Implement doctor checks and safe bootstrap**

`scripts/bootstrap.sh` must stop unless `python3.12` and Node `>=22.22.0` are available, create `.venv`, install the local package, pin AI-Search-Hub under `vendor/`, and install Promptfoo locally under `tools/promptfoo/`. It must not use `sudo`, replace system Python, install Docker, or touch browser credentials.

```python
# src/look4geo_probe/doctor.py
def summarize_checks(checks: list[dict]) -> dict:
    failed = [c["name"] for c in checks if c["required"] and not c["ok"]]
    return {"ok": not failed, "failed": failed}
```

- [ ] **Step 5: Add initial platform and routing configuration**

Define the eight platform IDs exactly: `doubao`, `deepseek`, `yuanbao`, `qwen`, `chatgpt`, `gemini`, `perplexity`, `grok`. Set AI-Search-Hub as primary for Doubao/Yuanbao/Qwen/Gemini/Grok and BrowserSkill as primary for DeepSeek/ChatGPT/Perplexity and fallback for the other browser platforms.

- [ ] **Step 6: Verify bootstrap artifacts without installing system-wide packages**

Run: `python3 -m pytest tests/test_doctor.py -v`

Expected: PASS.

Run: `bash -n scripts/bootstrap.sh`

Expected: exit 0.

- [ ] **Step 7: Commit**

```bash
git add .gitignore pyproject.toml config scripts tests/test_doctor.py src/look4geo_probe/doctor.py
git commit -m "build: add reproducible probe bootstrap and doctor"
```

### Task 2: Versioned Request and Result Models

**Files:**
- Create: `src/look4geo_probe/__init__.py`
- Create: `src/look4geo_probe/models.py`
- Create: `tests/test_models.py`

**Interfaces:**
- Consumes: Platform IDs from `config/platforms.yaml`.
- Produces: `ProbeRequest`, `RoutingDecision`, `PlatformAttempt`, `ProbeResult`, `JobStatus`, and `SCHEMA_VERSION` used by every later module.

- [ ] **Step 1: Write validation and serialization tests**

```python
import pytest
from pydantic import ValidationError
from look4geo_probe.models import ProbeRequest, ProbeResult, RouteMode


def test_blank_prompt_is_rejected():
    with pytest.raises(ValidationError):
        ProbeRequest(prompt="   ")


def test_manual_mode_requires_platforms():
    with pytest.raises(ValidationError):
        ProbeRequest(prompt="test", mode=RouteMode.MANUAL)


def test_result_schema_is_version_one():
    result = ProbeResult(job_id="job-1", prompt="test", status="succeeded")
    assert result.schema_version == 1
```

- [ ] **Step 2: Confirm the tests fail**

Run: `.venv/bin/pytest tests/test_models.py -v`

Expected: FAIL because the models are undefined.

- [ ] **Step 3: Implement strict models**

Use string enums for routing/job states, strip prompts, reject blank prompts, require a non-empty unique platform list in manual mode, and forbid unknown fields. `PlatformAttempt` must separate `raw_answer`, `normalized_answer`, `citations`, `diagnostic`, and `artifact_paths` so sanitization can target diagnostics without corrupting captured answers.

- [ ] **Step 4: Verify models**

Run: `.venv/bin/pytest tests/test_models.py -v`

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/__init__.py src/look4geo_probe/models.py tests/test_models.py
git commit -m "feat: define versioned probe data models"
```

### Task 3: Autonomous Router

**Files:**
- Create: `src/look4geo_probe/config.py`
- Create: `src/look4geo_probe/router.py`
- Create: `tests/test_router.py`

**Interfaces:**
- Consumes: `ProbeRequest`, platform/routing YAML, and `dict[str, PlatformHealth]`.
- Produces: `Router.route(request, health) -> RoutingDecision` with selected, excluded, reasons, and coverage gaps.

- [ ] **Step 1: Write routing tests for every mode and review-focus edge case**

```python
def test_auto_citation_prompt_prefers_perplexity(router, healthy):
    decision = router.route(request("请提供可核验引用的全球资料"), healthy)
    assert "perplexity" in decision.selected_platforms


def test_auto_mixed_market_balances_regions(router, healthy):
    decision = router.route(request("比较中国和海外新能源汽车品牌认知"), healthy)
    assert decision.domestic_count >= 1
    assert decision.international_count >= 1


def test_mixed_market_records_gap_when_international_unavailable(router, domestic_only):
    decision = router.route(request("比较中国和海外新能源汽车品牌认知"), domestic_only)
    assert decision.selected_platforms
    assert "international" in decision.coverage_gaps


def test_manual_never_substitutes_platform(router, healthy):
    decision = router.route(manual_request(["chatgpt"]), healthy | {"chatgpt": offline()})
    assert decision.selected_platforms == []
    assert decision.excluded["chatgpt"] == "offline"
```

- [ ] **Step 2: Confirm router tests fail**

Run: `.venv/bin/pytest tests/test_router.py -v`

Expected: FAIL because `Router` is undefined.

- [ ] **Step 3: Implement configuration loading and deterministic scoring**

Score explicit ecosystem/language/citation/realtime matches from YAML, then filter disabled, unhealthy, logged-out, rate-limited platforms. Break ties by configured priority followed by platform ID, so identical input and health snapshots produce identical routes.

- [ ] **Step 4: Verify all route modes**

Run: `.venv/bin/pytest tests/test_router.py -v`

Expected: PASS for auto, compare, all, manual, unavailable, and mixed-market cases.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/config.py src/look4geo_probe/router.py tests/test_router.py config
git commit -m "feat: add explainable autonomous platform routing"
```

### Task 4: SQLite Storage and Recoverable Job Lifecycle

**Files:**
- Create: `src/look4geo_probe/storage.py`
- Create: `src/look4geo_probe/jobs.py`
- Create: `tests/test_storage.py`
- Create: `tests/test_jobs.py`

**Interfaces:**
- Consumes: Versioned models from Task 2.
- Produces: `ProbeStore.create_job/get_job/save_attempt/save_result/recover_orphans` and `JobManager.submit/status/result`.

- [ ] **Step 1: Write persistence, uniqueness, and recovery tests**

```python
def test_duplicate_requests_get_distinct_jobs(store, request):
    first = store.create_job(request)
    second = store.create_job(request)
    assert first.job_id != second.job_id
    assert first.artifact_dir != second.artifact_dir


def test_recover_orphaned_running_job(store, running_job):
    recovered = store.recover_orphans()
    assert running_job.job_id in recovered
    assert store.get_job(running_job.job_id).status == "failed"
    assert "interrupted" in store.get_job(running_job.job_id).diagnostic
```

- [ ] **Step 2: Confirm storage tests fail**

Run: `.venv/bin/pytest tests/test_storage.py tests/test_jobs.py -v`

Expected: FAIL because storage/job modules are absent.

- [ ] **Step 3: Implement migrations, atomic writes, and job transitions**

Initialize SQLite with WAL mode and foreign keys, persist schema version, use UUIDv7-compatible sortable IDs or UUID4, and write JSON artifacts through a temporary file followed by atomic rename. Enforce allowed state transitions in `JobManager`.

- [ ] **Step 4: Verify concurrency and recovery behavior**

Run: `.venv/bin/pytest tests/test_storage.py tests/test_jobs.py -v`

Expected: PASS, including two same-prompt submissions and orphan recovery.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/storage.py src/look4geo_probe/jobs.py tests/test_storage.py tests/test_jobs.py
git commit -m "feat: persist recoverable probe jobs and results"
```

### Task 5: Adapter Contract, Sanitization, and Test Doubles

**Files:**
- Create: `src/look4geo_probe/adapters/base.py`
- Create: `src/look4geo_probe/adapters/registry.py`
- Create: `src/look4geo_probe/security.py`
- Create: `tests/fakes.py`
- Create: `tests/test_adapters.py`
- Create: `tests/test_security.py`

**Interfaces:**
- Consumes: `ProbeRequest`, `PlatformAttempt`, platform config.
- Produces: async `ProbeAdapter.health/login/run/cancel`, `AdapterRegistry.resolve`, and `sanitize_diagnostic`.

- [ ] **Step 1: Write registry fallback and field-specific redaction tests**

```python
def test_registry_uses_browser_fallback_when_primary_is_unhealthy(registry):
    adapter = registry.resolve("gemini", health={"ai_search_hub": False, "browser_skill": True})
    assert adapter.name == "browser_skill"


def test_diagnostic_is_redacted_but_raw_answer_is_preserved():
    attempt = make_attempt(raw_answer="Tokenization is useful", diagnostic="Authorization: Bearer sk-secret123")
    safe = sanitize_attempt(attempt)
    assert safe.raw_answer == "Tokenization is useful"
    assert "sk-secret123" not in safe.diagnostic
```

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_adapters.py tests/test_security.py -v`

Expected: FAIL because adapter/security modules are absent.

- [ ] **Step 3: Implement the adapter protocol and bounded fallback registry**

The registry may perform one configured fallback after a primary health/run failure. It must record the primary error and selected fallback. Manual mode may change adapter but never platform.

- [ ] **Step 4: Implement diagnostic sanitization**

Redact authorization/cookie headers, common API-key prefixes, and configured environment values from diagnostics and process output. Do not run broad replacements over user prompts or raw answers.

- [ ] **Step 5: Verify contract and sanitization**

Run: `.venv/bin/pytest tests/test_adapters.py tests/test_security.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/look4geo_probe/adapters src/look4geo_probe/security.py tests
git commit -m "feat: add safe probe adapter contract and fallback registry"
```

### Task 6: AI-Search-Hub and BrowserSkill Adapters

**Files:**
- Create: `src/look4geo_probe/adapters/ai_search_hub.py`
- Create: `src/look4geo_probe/adapters/browser_skill.py`
- Create: `src/look4geo_probe/parsers.py`
- Create: `tests/test_ai_search_hub_adapter.py`
- Create: `tests/test_browser_skill_adapter.py`
- Create: `tests/fixtures/adapter_outputs/`

**Interfaces:**
- Consumes: Adapter contract, pinned `vendor/AI-Search-Hub`, `bsk` CLI, Chrome login state.
- Produces: normalized `PlatformAttempt` records for all eight platform IDs.

- [ ] **Step 1: Add fixture-driven subprocess and parsing tests**

Mock subprocess execution; test success, timeout, login-required output, malformed JSON, selector failure, citations, and cancellation. Assert every started BrowserSkill session is stopped in `finally`, including failure paths.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_ai_search_hub_adapter.py tests/test_browser_skill_adapter.py -v`

Expected: FAIL because concrete adapters are absent.

- [ ] **Step 3: Implement AI-Search-Hub process adapter**

Invoke only `vendor/AI-Search-Hub/scripts/run_web_chat.py` with argument arrays, never shell interpolation. Capture stdout/stderr separately, enforce configured timeout, preserve raw output, and classify login/selector/rate-limit failures.

- [ ] **Step 4: Implement BrowserSkill session adapter**

Start a named no-focus session, navigate to the configured platform URL, execute the platform workflow, capture answer/citation evidence, and stop the session in all outcomes. When human login is required, return `waiting_for_login` with instructions rather than attempting credentials.

- [ ] **Step 5: Verify concrete adapters**

Run: `.venv/bin/pytest tests/test_ai_search_hub_adapter.py tests/test_browser_skill_adapter.py -v`

Expected: PASS without opening a real browser.

- [ ] **Step 6: Commit**

```bash
git add src/look4geo_probe/adapters src/look4geo_probe/parsers.py tests
git commit -m "feat: integrate AI Search Hub and BrowserSkill adapters"
```

### Task 7: Shared Probe Service and Async Execution

**Files:**
- Create: `src/look4geo_probe/service.py`
- Create: `src/look4geo_probe/worker.py`
- Create: `tests/test_service.py`

**Interfaces:**
- Consumes: Router, store, job manager, adapter registry.
- Produces: `ProbeService.run/status/result/platforms/login` used unchanged by CLI and MCP.

- [ ] **Step 1: Write service orchestration tests**

Test immediate submission, parallel platform attempts with configured concurrency, partial success, waiting-for-login, fallback evidence, and all-platform failure. Use fake adapters with controlled completion events to prove `run()` returns before answers complete.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_service.py -v`

Expected: FAIL because `ProbeService` is absent.

- [ ] **Step 3: Implement service and worker boundary**

`run()` validates/routes/persists then starts work and returns the persisted routing decision. Workers write each attempt independently, derive `partial`/`succeeded`/`failed`, and retain successful attempts when peers fail.

- [ ] **Step 4: Verify asynchronous behavior**

Run: `.venv/bin/pytest tests/test_service.py -v`

Expected: PASS and the immediate-return assertion completes before fake adapter release.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/service.py src/look4geo_probe/worker.py tests/test_service.py
git commit -m "feat: orchestrate asynchronous multi-platform probes"
```

### Task 8: CLI and MCP Interfaces

**Files:**
- Create: `src/look4geo_probe/cli.py`
- Create: `src/look4geo_probe/mcp_server.py`
- Create: `tests/test_cli.py`
- Create: `tests/test_mcp.py`

**Interfaces:**
- Consumes: `ProbeService` methods from Task 7.
- Produces: `probe` commands and MCP tools `probe_run`, `probe_status`, `probe_result`, `probe_platforms`, `probe_login`.

- [ ] **Step 1: Write CLI/MCP parity and schema tests**

Assert both interfaces pass identical `ProbeRequest` data to a fake service, MCP `probe_run` returns `{job_id, selected_platforms, reasons, status}`, unknown job IDs return readable structured errors, and CLI exit codes distinguish invalid input, auth required, partial, and internal failure.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_mcp.py -v`

Expected: FAIL because interfaces are absent.

- [ ] **Step 3: Implement Typer CLI**

Implement exactly: `run`, `status`, `result`, `platforms`, `login`, and `doctor`. Support JSON and Markdown result output without printing secrets.

- [ ] **Step 4: Implement stdio MCP server**

Expose exactly the five tools from the spec. Keep stdout reserved for MCP protocol messages and send diagnostics to stderr/log files.

- [ ] **Step 5: Verify interface parity**

Run: `.venv/bin/pytest tests/test_cli.py tests/test_mcp.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/look4geo_probe/cli.py src/look4geo_probe/mcp_server.py tests/test_cli.py tests/test_mcp.py
git commit -m "feat: expose probe through CLI and MCP"
```

### Task 9: Promptfoo Evaluation Bridge

**Files:**
- Create: `evals/promptfoo/promptfooconfig.yaml`
- Create: `evals/promptfoo/provider.py`
- Create: `evals/promptfoo/README.md`
- Create: `tests/test_promptfoo_provider.py`

**Interfaces:**
- Consumes: Probe CLI JSON output and Promptfoo custom Python provider contract.
- Produces: browser comparison target plus direct API baseline configuration.

- [ ] **Step 1: Write provider contract tests**

Test successful result extraction, partial results with metadata, timeout, and unknown job. Mock the CLI subprocess; no live browser or API key is required.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_promptfoo_provider.py -v`

Expected: FAIL because the provider is absent.

- [ ] **Step 3: Implement custom provider and minimal config**

The provider submits a `compare` job, waits with a finite polling interval/timeout, and returns combined text plus structured metadata. The YAML must reference environment variables by name and contain no credential values.

- [ ] **Step 4: Verify provider and Promptfoo config**

Run: `.venv/bin/pytest tests/test_promptfoo_provider.py -v`

Expected: PASS.

Run: `tools/promptfoo/node_modules/.bin/promptfoo validate -c evals/promptfoo/promptfooconfig.yaml`

Expected: configuration valid without launching paid evaluations.

- [ ] **Step 5: Commit**

```bash
git add evals/promptfoo tests/test_promptfoo_provider.py
git commit -m "feat: connect probe runs to Promptfoo evaluations"
```

### Task 10: Codex and WorkBuddy Packaging

**Files:**
- Create: `connectors/codex/SKILL.md`
- Create: `connectors/codex/config.example.toml`
- Create: `connectors/workbuddy/connector-meta.json`
- Create: `connectors/workbuddy/mcp.json`
- Create: `connectors/workbuddy/icon.svg`
- Create: `connectors/workbuddy/skills/look4geo-probe/SKILL.md`
- Create: `tests/test_connectors.py`

**Interfaces:**
- Consumes: Local `python -m look4geo_probe.mcp_server` entry point and MCP tool schemas.
- Produces: installable Codex instructions and a WorkBuddy MCP + Skill connector package.

- [ ] **Step 1: Write connector validation tests**

Validate JSON syntax, required WorkBuddy metadata, exactly one stdio MCP server, relative/portable command arguments, required icon, both Skill frontmatters, and absence of token/key/cookie literals.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_connectors.py -v`

Expected: FAIL because connector files are absent.

- [ ] **Step 3: Create Codex Skill and MCP configuration example**

Teach Codex when to use auto/compare/manual, how to poll jobs, and how to surface login requests. Configure the MCP command with the project's absolute `.venv` Python only in the local installation copy; keep the committed example portable.

- [ ] **Step 4: Create WorkBuddy connector and Skill**

Use `type: stdio`, runtime metadata compatible with the installed WorkBuddy version, Chinese and English examples, and no secrets. The Skill must tell WorkBuddy to call `probe_status`/`probe_result` after `probe_run` rather than waiting in one request.

- [ ] **Step 5: Verify packages**

Run: `.venv/bin/pytest tests/test_connectors.py -v`

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add connectors tests/test_connectors.py
git commit -m "feat: package Look4GEO for Codex and WorkBuddy"
```

### Task 11: Installation, Login Runbook, and End-to-End Verification

**Files:**
- Create: `README.md`
- Create: `docs/installation.md`
- Create: `docs/login-and-recovery.md`
- Create: `scripts/smoke_test.py`
- Create: `tests/test_smoke_test.py`

**Interfaces:**
- Consumes: All deliverables from Tasks 1–10 and user-assisted browser login.
- Produces: Verified local installation, documented unified commands, and reproducible smoke-test report under `data/runs/`.

- [ ] **Step 1: Write smoke-runner tests with fake adapters**

Test doctor failure short-circuit, domestic/overseas probe selection, MCP discovery failure, partial result reporting, and report-path output.

- [ ] **Step 2: Confirm tests fail**

Run: `.venv/bin/pytest tests/test_smoke_test.py -v`

Expected: FAIL because the smoke runner is absent.

- [ ] **Step 3: Implement the smoke runner and operating documentation**

Document exact setup, browser extension pairing, each platform login URL, CAPTCHA handoff, logout recovery, MCP registration for both clients, unified commands, backup of non-secret results, and update/rollback procedures. The smoke runner must never automate credential entry.

- [ ] **Step 4: Run the complete automated suite**

Run: `.venv/bin/pytest -q`

Expected: all tests pass.

- [ ] **Step 5: Run static operational checks**

Run: `.venv/bin/probe doctor --json`

Expected: required checks report healthy after dependency installation; optional Docker reports absent without failing V1.

Run: `git grep -nE '(sk-[A-Za-z0-9_-]{12,}|Authorization: Bearer|cookie=)' -- ':!docs/superpowers'`

Expected: no credential-like committed values.

- [ ] **Step 6: Perform user-assisted browser smoke tests**

Run one logged-in AI-Search-Hub domestic platform and one BrowserSkill overseas platform, then run a mixed-market `auto` request. Confirm raw answers, routing reasons, citations when available, and artifacts are stored; confirm no credential material appears in result files.

- [ ] **Step 7: Verify Codex and WorkBuddy MCP discovery**

Invoke `probe_platforms` from each client, submit a no-cost browser probe, poll status, and fetch the result. Record client/version and outcome in the smoke report.

- [ ] **Step 8: Commit**

```bash
git add README.md docs scripts/smoke_test.py tests/test_smoke_test.py
git commit -m "docs: complete deployment and verification runbook"
```

### Task 12: Final Verification and Release Baseline

**Files:**
- Modify: `README.md`
- Create: `CHANGELOG.md`

**Interfaces:**
- Consumes: Passing automated and live smoke tests.
- Produces: Auditable `v0.1.0` local release baseline.

- [ ] **Step 1: Re-run all verification commands from a clean shell**

Run: `.venv/bin/pytest -q`

Expected: all tests pass.

Run: `.venv/bin/probe doctor --json`

Expected: all V1 required checks pass.

Run: `.venv/bin/probe platforms --json`

Expected: all eight platform IDs appear with explicit availability and login state.

- [ ] **Step 2: Review the diff and repository status**

Run: `git diff --check && git status --short`

Expected: no whitespace errors; only intentional release files remain unstaged.

- [ ] **Step 3: Record release behavior**

Add `0.1.0` to `CHANGELOG.md` with platform coverage, autonomous modes, MCP/CLI interfaces, known UI-automation limitations, and exact smoke-test outcomes. Update README only with commands proven during verification.

- [ ] **Step 4: Commit and tag locally**

```bash
git add README.md CHANGELOG.md
git commit -m "chore: establish Look4GEO Probe v0.1.0 baseline"
git tag -a v0.1.0 -m "Look4GEO Probe V1"
```

- [ ] **Step 5: Connect a remote only after the user provides its URL**

Run: `git remote add origin <user-provided-url>`

Expected: `git remote -v` shows only the explicitly supplied repository. Do not create or push to a remote without the user's repository URL and authorization.
