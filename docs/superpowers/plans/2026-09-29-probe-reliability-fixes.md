# Probe Reliability Fixes Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove the remaining browser contention, stale-source, unsafe fallback-profile, and health-check failure modes reported by WorkBuddy without changing the probe's private local architecture.

**Architecture:** Serialize BrowserSkill operations with a machine-local file lock, constrain source discovery to the newest answer and visible matching panel, and launch AI-Search-Hub against its existing isolated profile without reseeding or bulk deletion. Treat the current passing `doctor` result as an environmental recovery and add no speculative code unless it fails reproducibly.

**Tech Stack:** Python 3.12, asyncio, `fcntl`, pytest, BrowserSkill CLI, AI-Search-Hub submodule.

**Spec:** `/Users/sunkai/WorkBuddy/2026-09-23-23-36-22/probe-wenqing-geo/探针运行失败与错误记录.md`

## Global Constraints

- Remain machine-local and callable from both Codex and WorkBuddy.
- Preserve explicit platform selection and honest failure accounting.
- Do not delete or reset browser profiles.
- Write a failing regression test before each production change.
- Do not modify `doctor` while its real command passes.

## Review Focus

- Two independent CLI processes must not control the shared Agent Window simultaneously.
- A stale document-level source trigger must not be used for Doubao.
- Hidden or unrelated dialogs must not be interpreted as the current answer's source panel.
- AI-Search-Hub must not reseed or clear its profile during fallback.
- Lock release must occur after exceptions so later probes can continue.

---

### Task 1: Cross-process BrowserSkill serialization

**Files:**
- Create: `src/look4geo_probe/browser_lock.py`
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Test: `tests/test_browser_lock.py`
- Test: `tests/test_browser_skill_adapter.py`

- [ ] Write failing tests proving a second process waits and exceptions release the lock.
- [ ] Implement an async context manager backed by non-blocking `fcntl.flock`.
- [ ] Wrap each complete BrowserSkill adapter run in the shared lock.
- [ ] Run focused tests and commit.

### Task 2: Bind source collection to the current answer

**Files:**
- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Test: `tests/test_browser_skill_adapter.py`

- [ ] Write failing tests proving Doubao does not fall back to a document-level trigger and hidden/unlabelled dialogs are ignored.
- [ ] Add a current-answer-only trigger scope for Doubao and filter panel candidates to visible, label-matching containers.
- [ ] Run focused tests and commit.

### Task 3: Prevent AI-Search-Hub profile resets

**Files:**
- Modify: `src/look4geo_probe/adapters/ai_search_hub.py`
- Test: `tests/test_ai_search_hub_adapter.py`

- [ ] Write a failing test requiring `--debug-profile-dir` and `--user-data-source` to point to the same existing isolated profile.
- [ ] Add those arguments without modifying the pinned submodule.
- [ ] Run focused tests and commit.

### Task 4: Verify health and full integration

**Files:**
- Modify only if a reproducible failure remains.

- [ ] Run `scripts/probe-local doctor --json` and require a healthy summary.
- [ ] Run the complete pytest suite and `git diff --check`.
- [ ] Run one real serialized browser probe and inspect adapter/source diagnostics.
- [ ] Push the verified commits to `main` and restart WorkBuddy if code changed.
