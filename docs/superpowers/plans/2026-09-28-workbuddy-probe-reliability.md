# WorkBuddy Probe Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make WorkBuddy's local probe runs recover cleanly from current Grok, Gemini, Perplexity, and fallback failures without falsifying measurement results.

**Architecture:** Keep the single local CLI/MCP contract. Harden each adapter at its existing boundary, represent Perplexity quota exhaustion as a suspended batch state, and avoid unsafe AI-Search-Hub cleanup rather than granting broad delete authority.

**Tech Stack:** Python 3.12, asyncio, Playwright/Camoufox, BrowserSkill CLI, pytest.

**Spec:** `/tmp/codex-remote-attachments/01a0d2f2-32fc-7861-9f2a-5f4676348e1a/11171EC9-C5E0-431A-89B5-07E995F8124F/1-执行问题汇总.md`

## Global Constraints

- Remain private and machine-local; Codex and WorkBuddy use the same `scripts/probe-local` interface.
- Perplexity quota exhaustion is not a negative GEO observation and must not be retried repeatedly.
- Free Perplexity Standard officially provides 3 Pro Searches per day; exact reset time is not guaranteed, so resume only after a later probe confirms availability.
- Never approve or automate bulk deletion of browser profile data.
- Preserve answer status separately from source capture status.

## Review Focus

- Grok aria-label changes or focus loss must trigger re-observation and verified refill.
- Playwright-specific timeout exceptions must become structured timeout attempts and permit eligible fallback.
- Camoufox sandbox relaxation must be explicit in private local configuration, not globally applied.
- Perplexity quota state must stop later Perplexity cells while allowing other platforms to continue.
- Perplexity sources must exclude stale cross-conversation source-panel cards.

---

### Task 1: Harden Grok input and browser exception handling

**Files:**
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `src/look4geo_probe/adapters/registry.py`
- Test: `tests/test_browser_skill_adapter.py`
- Test: `tests/test_adapters.py`

**Interfaces:**
- Consumes: existing `BskCliClient.probe` and `AdapterChain.run`.
- Produces: verified Grok prompt entry and structured timeout fallback.

- [ ] Write failing tests for `Ask Grok anything`, refill after target loss, and Playwright timeout fallback.
- [ ] Run focused tests and confirm red.
- [ ] Add the label, generalize prompt readback/re-observation for Grok, and classify Playwright timeout.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 2: Make WorkBuddy Camoufox sandbox configuration explicit

**Files:**
- Modify: `src/look4geo_probe/adapters/camoufox_gemini.py`
- Modify: `src/look4geo_probe/runtime.py`
- Modify: `config/local.env.example`
- Modify: `connectors/workbuddy/README.md`
- Test: `tests/test_camoufox_gemini_adapter.py`
- Test: `tests/test_connectors.py`

**Interfaces:**
- Consumes: `LOOK4GEO_CAMOUFOX_DISABLE_CONTENT_SANDBOX`.
- Produces: a scoped environment setting only while Camoufox is active.

- [ ] Write failing launch-option/environment restoration tests.
- [ ] Run focused tests and confirm red.
- [ ] Implement scoped sandbox environment handling and document the private setting.
- [ ] Add the setting to this Mac's ignored `config/local.env`.
- [ ] Run focused and full tests.
- [ ] Commit.

### Task 3: Enforce Perplexity quota suspension and cited-only sources

**Files:**
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `/Users/sunkai/WorkBuddy/originature/geo-recheck-20260928/run_batch.py`
- Test: `tests/test_browser_skill_adapter.py`
- Create: `/Users/sunkai/WorkBuddy/originature/geo-recheck-20260928/test_run_batch.py`

**Interfaces:**
- Consumes: `failure=rate_limited` from probe JSON.
- Produces: manifest status `suspended_quota`, a next-check timestamp, and retry-failed behavior after recovery.

- [ ] Write failing tests that Perplexity ignores source panels and that the batch stops later Perplexity cells after quota exhaustion.
- [ ] Run focused tests and confirm red.
- [ ] Disable Perplexity surfaced-panel collection; keep answer-DOM cited links.
- [ ] Add quota suspension metadata using a conservative next-day check, while allowing non-Perplexity cells to continue.
- [ ] Document that page recovery, not a guessed clock time, authorizes resumed collection.
- [ ] Run focused and full tests.
- [ ] Commit repository changes; preserve WorkBuddy output data.

### Task 4: Safe fallback policy and real checks

**Files:**
- Modify: `src/look4geo_probe/runtime.py`
- Modify: `tests/test_connectors.py`
- Create: `docs/verification/2026-09-28-workbuddy-reliability-check.md`

**Interfaces:**
- Consumes: hardened adapters from Tasks 1–3.
- Produces: fallback chains that do not invoke unsafe AI-Search-Hub profile cleanup for Grok.

- [ ] Write a failing routing test for the safe Grok chain.
- [ ] Remove AI-Search-Hub from Grok's automatic chain; retain it only where already proven safe.
- [ ] Run doctor, Grok smoke, Gemini smoke, and a Perplexity quota-state check without repeated retries.
- [ ] Record exact outcomes and remaining external limits.
- [ ] Run the full test suite and `git diff --check`.
- [ ] Commit and push `main` after verification.
