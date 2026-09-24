# WorkBuddy Browser Runtime Repair Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every Look4GEO web platform runnable from WorkBuddy through the existing BrowserSkill browser and restore the private Promptfoo runtime.

**Architecture:** Add BrowserSkill site definitions for the five platforms currently tied to AI-Search-Hub, then select BrowserSkill at runtime when `LOOK4GEO_RUNTIME=workbuddy`. Keep AI-Search-Hub available outside WorkBuddy and never delete or rebuild browser profiles automatically. Resolve the project-local Node and Promptfoo executables explicitly so WorkBuddy's reduced `PATH` does not produce false negatives.

**Tech Stack:** Python 3.12, BrowserSkill CLI, Pydantic, pytest, Node.js, Promptfoo.

**Spec:** `docs/superpowers/specs/2026-09-24-look4geo-probe-v1-design.md`

## Global Constraints

- Browser credentials and login state remain in the user's existing local Chrome profile.
- Failed probes remain failures and are never counted as non-mentions.
- Manual platform selection is never silently substituted.
- No browser profile deletion or bulk cleanup is permitted.
- Existing Codex and CLI interfaces remain compatible.

## Review Focus

- WorkBuddy starts with a reduced `PATH`: doctor must locate the configured private Node runtime.
- A platform page exposes a composer without a stable accessibility label: selector discovery must still work.
- A page remains loading with a disabled send control: report a precise send failure without duplicate submissions.
- BrowserSkill is unavailable: platform health must be unavailable instead of falling back silently in manual mode.
- Normal Codex runtime must retain the configured AI-Search-Hub primary adapters.

---

### Task 1: WorkBuddy runtime routing

**Files:**
- Modify: `src/look4geo_probe/runtime.py`
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Test: `tests/test_browser_skill_adapter.py`
- Test: `tests/test_connectors.py`

**Interfaces:**
- Consumes: `LOOK4GEO_RUNTIME`, `LOOK4GEO_BROWSER_ID`.
- Produces: `build_adapters(root, runtime)` and BrowserSkill definitions for all eight platform IDs.

- [ ] Add failing tests proving WorkBuddy routes all eight web platforms through BrowserSkill while the default runtime preserves current primaries.
- [ ] Run the focused tests and confirm failure is caused by missing runtime routing.
- [ ] Add the five site definitions and minimal runtime adapter selection.
- [ ] Run focused tests and the complete suite.
- [ ] Commit the runtime routing change.

### Task 2: Reliable browser submission readiness

**Files:**
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Test: `tests/test_browser_skill_adapter.py`

**Interfaces:**
- Consumes: BrowserSkill `evaluate` results.
- Produces: selector fallback for composers and a bounded send-control readiness check.

- [ ] Add failing tests for selector-only composers and disabled send controls.
- [ ] Run focused tests and confirm both failures.
- [ ] Implement selector discovery plus bounded readiness polling without duplicate sends.
- [ ] Run focused tests and the complete suite.
- [ ] Commit the browser readiness change.

### Task 3: Private Node and Promptfoo runtime

**Files:**
- Modify: `src/look4geo_probe/doctor.py`
- Modify: `config/local.env.example`
- Test: `tests/test_doctor.py`

**Interfaces:**
- Consumes: `LOOK4GEO_NODE`, project-local `tools/promptfoo/node_modules/.bin/promptfoo`.
- Produces: environment-independent doctor checks.

- [ ] Add a failing test proving an explicit Node path works with an empty `PATH`.
- [ ] Implement explicit Node resolution and document the local variable.
- [ ] Install Promptfoo under `tools/promptfoo` using the resolved Node/npm runtime.
- [ ] Run doctor from the WorkBuddy directory and validate Promptfoo configuration.
- [ ] Run the complete test suite and commit.

### Task 4: Real smoke verification

**Files:**
- No production files unless a reproduced defect requires a new TDD cycle.

**Interfaces:**
- Consumes: `scripts/probe-local` from a WorkBuddy working directory.
- Produces: one successful existing-platform smoke result and explicit status for unavailable logins.

- [ ] Run `doctor` from the WorkBuddy directory.
- [ ] Run a DeepSeek control probe through BrowserSkill.
- [ ] Run a ChatGPT probe only if its page becomes ready; otherwise preserve the environmental failure evidence.
- [ ] Verify no tracked credentials, browser state, or debug evidence entered Git.
- [ ] Record final verified status and remaining login requirements.
