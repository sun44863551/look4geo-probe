# Look4GEO Probe Visible Source Capture Implementation Plan

> **For agent:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the private local Look4GEO Probe so all eight supported platforms report browser-visible answer citations and source-panel results separately, while preserving the existing CLI, routing, answer extraction, and legacy `citations` output.

**Architecture:** Add a versioned source result contract and a pure normalization/deduplication module, then extend the existing BrowserSkill adapter with one shared collector driven by per-platform configuration. Roll the collector out in domestic and international platform batches using real DOM observations, map fallback-adapter URLs into the same contract, and verify the unchanged local CLI from both Codex and WorkBuddy-facing connectors. Source inspection is best-effort: it may report `captured`, `none_exposed`, or `failed`, but must never turn a successful answer into a failed answer.

**Tech Stack:** Python 3.11+, Pydantic v2, asyncio, BrowserSkill `bsk` CLI, pytest/pytest-asyncio, SQLite JSON storage, local Codex and WorkBuddy skill connectors.

---

## Guardrails and execution order

- Start from the Git-backed V1 recovery point `look4geo-probe-v1` (`dbf61ba`); do not move or overwrite that tag.
- Implement on a `codex/visible-source-capture` branch created from the current approved design commit.
- Never commit cookies, browser profiles, raw private page HTML, login screenshots, or generated run data.
- Use one BrowserSkill session at a time. The logged-in browser is shared local state and must not be exercised concurrently by platform tests.
- For every production behavior, create and run the failing test first, then add the minimum implementation, rerun the focused test, and only then run the wider regression set.
- Commit after each task. If a task uncovers an architectural change, update the approved design before changing the implementation.

## Task 1: Add the version-2 source result contract

**Files:**

- Modify: `src/look4geo_probe/models.py`
- Modify: `tests/test_models.py`
- Modify: `tests/test_storage.py`

- [ ] **Step 1: Write failing model tests**

Add tests asserting:

- `ProbeResult(...).schema_version == 2`.
- `SourceRecord` serializes the exact fields `url`, `title`, `domain`, `snippet`, `source_role`, `evidence_origin`, and `linked_in_answer`.
- a newly created `PlatformAttempt` defaults to `sources=[]`, `source_capture_status="none_exposed"`, and no source diagnostic.
- a schema-version-1 result payload without the new fields still validates and preserves `schema_version == 1`.

Run:

```bash
pytest -q tests/test_models.py
```

Expected: FAIL because the source enums/models and schema version do not exist yet.

- [ ] **Step 2: Implement the data model**

In `src/look4geo_probe/models.py`:

- set `SCHEMA_VERSION = 2`;
- add `SourceRole`, `SourceEvidenceOrigin`, and `SourceCaptureStatus` as `StrEnum` classes;
- add `SourceRecord(StrictModel)` with the approved fields;
- extend `PlatformAttempt` with:

```python
sources: list[SourceRecord] = Field(default_factory=list)
source_capture_status: SourceCaptureStatus = SourceCaptureStatus.NONE_EXPOSED
source_capture_diagnostic: str | None = None
```

Do not constrain `ProbeResult.schema_version` to only version 2; old stored payloads must remain valid.

- [ ] **Step 3: Add and run storage compatibility tests**

Add a storage test that writes a completed version-1 `result_json` without the new fields, reloads it through `ProbeStore`, and verifies the attempt receives the new defaults. Add a second test proving a version-2 result with one source round-trips unchanged.

Run:

```bash
pytest -q tests/test_models.py tests/test_storage.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/look4geo_probe/models.py tests/test_models.py tests/test_storage.py
git commit -m "feat: add visible source result schema"
```

## Task 2: Build pure URL normalization and source merging

**Files:**

- Create: `src/look4geo_probe/sources.py`
- Create: `tests/test_sources.py`

- [ ] **Step 1: Write failing normalization tests**

Cover these public functions:

```python
normalize_source_url(
    url: str,
    *,
    redirect_params: tuple[str, ...] = ("url", "target", "dest", "destination"),
    excluded_domains: frozenset[str] = frozenset(),
) -> str | None

merge_sources(records: list[SourceRecord]) -> list[SourceRecord]

citations_from_sources(records: list[SourceRecord]) -> list[Citation]
```

Tests must prove that normalization:

- accepts only HTTP(S);
- lowercases hostnames and removes fragments;
- removes `utm_*`, `gclid`, and `fbclid` while retaining content-identifying query parameters;
- unwraps a normal URL-valued redirect query parameter once;
- rejects excluded platform domains, including subdomains;
- returns `None` for malformed and platform-internal navigation URLs.

Tests must prove that merging:

- deduplicates by normalized URL without changing first-seen order;
- promotes `surfaced` to `cited` when the same URL appears in the answer;
- prefers `answer_dom` evidence for cited duplicates;
- retains the first non-empty title and snippet;
- derives legacy citations only from final `cited` records.

Run:

```bash
pytest -q tests/test_sources.py
```

Expected: FAIL because `look4geo_probe.sources` does not exist.

- [ ] **Step 2: Implement the pure source module**

Create `src/look4geo_probe/sources.py` with no browser or storage dependency. Add an internal helper for suffix-safe excluded-domain matching and use `urllib.parse` for URL handling. Do not use a broad tracking-parameter allowlist that could delete content identifiers.

- [ ] **Step 3: Run focused and model regression tests**

```bash
pytest -q tests/test_sources.py tests/test_models.py tests/test_storage.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add src/look4geo_probe/sources.py tests/test_sources.py
git commit -m "feat: normalize and merge visible sources"
```

## Task 3: Add the shared BrowserSkill source collector contract

**Files:**

- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `tests/test_browser_skill_adapter.py`

- [ ] **Step 1: Write failing adapter-contract tests**

Extend test doubles so a stable answer page can return structured `answer_links` instead of only URL strings. Add tests asserting:

- `BrowserProbeOutput` carries `sources`, `source_capture_status`, and `source_capture_diagnostic`;
- answer links become `cited` / `answer_dom` records;
- missing panel trigger plus no answer links becomes `none_exposed`;
- a panel trigger that is found but cannot be opened becomes `failed` while `answer` remains populated;
- a panel that opens with no valid external card becomes `none_exposed`;
- a stale panel reference is re-observed once and not retried indefinitely.

Run:

```bash
pytest -q tests/test_browser_skill_adapter.py
```

Expected: FAIL because the browser output and source collector do not exist.

- [ ] **Step 2: Add configuration keys without inventing selectors**

Extend each `PLATFORMS` entry with these data keys, initially using empty tuples where live evidence has not yet been recorded:

```python
"source_trigger_labels": (...),
"source_trigger_selectors": (...),
"source_panel_selectors": (...),
"source_card_selectors": (...),
"excluded_source_domains": (...),
```

An empty trigger configuration means staged `unsupported`; it must be eliminated platform-by-platform in Tasks 5 and 6.

- [ ] **Step 3: Implement one generic collector**

Add a private BrowserSkill result shape for raw DOM candidates and implement:

```python
async def _collect_visible_sources(
    self,
    session_id: str,
    platform: str,
    answer_links: list[dict[str, str]],
) -> tuple[list[SourceRecord], SourceCaptureStatus, str | None]
```

The collector must:

- create cited candidates from the current answer boundary;
- locate and open only a platform-owned source trigger;
- wait for the owned panel to stabilize for two observations;
- collect `href`, visible title, and visible snippet from configured cards;
- never click a source card or navigate externally;
- normalize and merge through `sources.py`;
- return `failed` diagnostics locally rather than raise into answer processing.

Change `_answer_page()` to return `answer_links` entries containing at least `url` and visible anchor text. Keep `links` temporarily if existing tests or callers need it during the transition.

- [ ] **Step 4: Invoke source inspection only after answer stability**

When the answer reaches two stable rounds, call `_collect_visible_sources()` and return the answer regardless of the source status. Source inspection exceptions must be converted to `SourceCaptureStatus.FAILED` with a concise diagnostic.

- [ ] **Step 5: Run focused regression tests**

```bash
pytest -q tests/test_browser_skill_adapter.py tests/test_sources.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/look4geo_probe/adapters/browser_skill.py tests/test_browser_skill_adapter.py
git commit -m "feat: add shared browser source collector"
```

## Task 4: Map source results into attempts and fallback adapters

**Files:**

- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `src/look4geo_probe/adapters/ai_search_hub.py`
- Modify: `tests/test_browser_skill_adapter.py`
- Modify: `tests/test_ai_search_hub_adapter.py`
- Modify: `tests/test_adapters.py`

- [ ] **Step 1: Write failing mapping and preservation tests**

Add tests asserting:

- `BrowserSkillAdapter.run()` copies all source fields into `PlatformAttempt`;
- legacy `citations` exactly equal `citations_from_sources(output.sources)`;
- source status `failed` still produces `JobStatus.SUCCEEDED` when the answer succeeded;
- login, rate-limit, send, extraction, and timeout attempts do not fabricate sources;
- AI-Search-Hub answer URLs become `cited` / `answer_dom` records and legacy citations remain unchanged;
- a successful AI-Search-Hub answer with no URLs reports `none_exposed`.

Run:

```bash
pytest -q tests/test_browser_skill_adapter.py tests/test_ai_search_hub_adapter.py tests/test_adapters.py
```

Expected: FAIL on missing source mapping.

- [ ] **Step 2: Implement BrowserSkill mapping**

Change `BrowserProbeOutput` to carry normalized `SourceRecord` objects. In `BrowserSkillAdapter.run()`, assign the source fields and derive `citations` from the final source list. Do not duplicate URL parsing inside the adapter.

- [ ] **Step 3: Implement honest fallback mapping**

In `AIHubAdapter`, URLs visible in its returned answer may be represented only as cited sources because the fallback does not safely expose a source panel. Normalize them through `sources.py`. Use `none_exposed` when there are no valid URLs; do not label hidden or unknown sources as `surfaced`.

- [ ] **Step 4: Run tests and commit**

```bash
pytest -q tests/test_browser_skill_adapter.py tests/test_ai_search_hub_adapter.py tests/test_adapters.py
git add src/look4geo_probe/adapters/browser_skill.py src/look4geo_probe/adapters/ai_search_hub.py tests/test_browser_skill_adapter.py tests/test_ai_search_hub_adapter.py tests/test_adapters.py
git commit -m "feat: expose sources through probe attempts"
```

## Task 5: Configure and verify the four domestic platforms

**Files:**

- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `tests/test_browser_skill_adapter.py`
- Create: `tests/fixtures/source_dom/doubao.json`
- Create: `tests/fixtures/source_dom/deepseek.json`
- Create: `tests/fixtures/source_dom/yuanbao.json`
- Create: `tests/fixtures/source_dom/qwen.json`

- [ ] **Step 1: Observe each live page read-only**

Using isolated BrowserSkill sessions, inspect one completed, search-enabled answer for Doubao, DeepSeek, Yuanbao, and Qwen. Record only stable public structural evidence needed to identify:

- the current answer boundary;
- source trigger text or accessible label;
- source panel boundary;
- source card links, titles, and snippets;
- internal domains or URL patterns that must be excluded.

Sanitize the observations into minimal fixture JSON. Do not save raw page HTML, account details, conversation IDs, or cookies.

- [ ] **Step 2: Write failing fixture-driven tests**

For each platform, add tests for cited extraction, surfaced extraction, duplicate promotion, no-source behavior, and panel-open failure. Assert `SourceRecord` output, not exact selector strings.

Run:

```bash
pytest -q tests/test_browser_skill_adapter.py -k 'doubao or deepseek or yuanbao or qwen'
```

Expected: FAIL until platform configuration matches observed pages.

- [ ] **Step 3: Add the minimum platform configuration**

Populate the five source-capture configuration keys for each domestic platform. If one platform requires a safe structural variation, add it behind a named configuration flag rather than a platform-name branch in normalization or deduplication code.

- [ ] **Step 4: Run domestic and complete adapter tests**

```bash
pytest -q tests/test_browser_skill_adapter.py -k 'doubao or deepseek or yuanbao or qwen'
pytest -q tests/test_browser_skill_adapter.py
```

Expected: PASS; none of the four domestic platforms remains intentionally `unsupported`.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/adapters/browser_skill.py tests/test_browser_skill_adapter.py tests/fixtures/source_dom/doubao.json tests/fixtures/source_dom/deepseek.json tests/fixtures/source_dom/yuanbao.json tests/fixtures/source_dom/qwen.json
git commit -m "feat: capture visible sources on domestic platforms"
```

## Task 6: Configure and verify the four international platforms

**Files:**

- Modify: `src/look4geo_probe/adapters/browser_skill.py`
- Modify: `tests/test_browser_skill_adapter.py`
- Create: `tests/fixtures/source_dom/chatgpt.json`
- Create: `tests/fixtures/source_dom/gemini.json`
- Create: `tests/fixtures/source_dom/perplexity.json`
- Create: `tests/fixtures/source_dom/grok.json`

- [ ] **Step 1: Observe each live page read-only**

Repeat the sanitized structural inspection from Task 5 for ChatGPT, Gemini, Perplexity, and Grok. For Perplexity, confirm that source-card discovery stays scoped to the selected longest answer and does not treat suggested follow-ups as sources.

- [ ] **Step 2: Write failing fixture-driven tests**

For each platform, cover cited extraction, surfaced extraction, duplicate promotion, no-source behavior, and panel-open failure.

Run:

```bash
pytest -q tests/test_browser_skill_adapter.py -k 'chatgpt or gemini or perplexity or grok'
```

Expected: FAIL until configuration matches observed pages.

- [ ] **Step 3: Add the minimum platform configuration**

Populate the source keys for the four platforms. Keep special behavior declarative where possible; do not add separate scrapers.

- [ ] **Step 4: Run international and complete adapter tests**

```bash
pytest -q tests/test_browser_skill_adapter.py -k 'chatgpt or gemini or perplexity or grok'
pytest -q tests/test_browser_skill_adapter.py
```

Expected: PASS; none of the four international platforms remains intentionally `unsupported`.

- [ ] **Step 5: Commit**

```bash
git add src/look4geo_probe/adapters/browser_skill.py tests/test_browser_skill_adapter.py tests/fixtures/source_dom/chatgpt.json tests/fixtures/source_dom/gemini.json tests/fixtures/source_dom/perplexity.json tests/fixtures/source_dom/grok.json
git commit -m "feat: capture visible sources on international platforms"
```

## Task 7: Verify CLI, storage, MCP, Codex, and WorkBuddy compatibility

**Files:**

- Modify: `tests/test_cli.py`
- Modify: `tests/test_mcp.py`
- Modify: `tests/test_connectors.py`
- Modify: `connectors/codex/SKILL.md`
- Modify: `connectors/workbuddy/skills/look4geo-probe/SKILL.md`
- Modify: `connectors/workbuddy/README.md`

- [ ] **Step 1: Write failing contract tests**

Add tests showing that:

- the existing `scripts/probe-local` invocation and platform selection flags are unchanged;
- CLI and MCP JSON contain `sources`, `source_capture_status`, and `source_capture_diagnostic`;
- legacy `citations` remains present;
- both local connector instructions invoke the repository-local CLI and do not publish or install a public WorkBuddy skill;
- a source collection failure is displayed separately from the answer status.

Run:

```bash
pytest -q tests/test_cli.py tests/test_mcp.py tests/test_connectors.py
```

Expected: FAIL where documentation or output assertions do not yet describe source fields.

- [ ] **Step 2: Update local connector documentation**

Document the source truth boundary and the meanings of `cited`, `surfaced`, `captured`, `none_exposed`, and `failed`. Include examples for manual one-platform selection and multi-platform selection, without changing command names or moving the project into WorkBuddy's public skill area.

- [ ] **Step 3: Run connector and service regression tests**

```bash
pytest -q tests/test_cli.py tests/test_mcp.py tests/test_connectors.py tests/test_service.py tests/test_jobs.py tests/test_router.py
```

Expected: PASS.

- [ ] **Step 4: Commit**

```bash
git add tests/test_cli.py tests/test_mcp.py tests/test_connectors.py connectors/codex/SKILL.md connectors/workbuddy/skills/look4geo-probe/SKILL.md connectors/workbuddy/README.md
git commit -m "docs: expose source capture to local callers"
```

## Task 8: Run automated gates and an eight-platform real-browser matrix

**Files:**

- Create: `docs/verification/2026-09-26-visible-source-capture.md`
- Modify only if a failing test identifies a defect: production/test files from Tasks 1-7

- [ ] **Step 1: Run the complete automated suite**

```bash
pytest -q
python scripts/doctor.py --json
```

Expected: all tests pass; doctor reports the local BrowserSkill/Codex/WorkBuddy dependencies accurately. Fix any defect test-first and commit the fix separately.

- [ ] **Step 2: Run the existing validity gate**

Use the repository's current validity command or tests, confirmed from `tests/test_validity.py`, and require a clean result before live testing.

```bash
pytest -q tests/test_validity.py tests/test_security.py
```

Expected: PASS with no contamination or secret-handling regression.

- [ ] **Step 3: Run one bounded real probe per platform**

Use the unchanged local CLI in manual routing mode with the same search-enabled prompt for:

```text
doubao, deepseek, yuanbao, qwen, chatgpt, gemini, perplexity, grok
```

Use `repeats=1` for this engineering verification. Run platforms sequentially. For transient login, rate-limit, or timeout conditions, permit at most three bounded attempts and record every final status honestly. Do not convert an answer timeout into source success; Qwen follows the limitation in the approved design.

- [ ] **Step 4: Audit each result against the visible page**

For every platform, compare the stored JSON with the visible answer and source panel. Record:

- answer status;
- cited count;
- surfaced count;
- total deduplicated sources;
- source-capture status;
- concise diagnostic;
- local result artifact path;
- whether visual spot-check matched the stored result.

A zero-source result passes only when the UI visibly exposes no valid external source and status is `none_exposed`. A successful answer with a broken panel must remain answer-success plus source `failed`.

- [ ] **Step 5: Write the verification report**

Create `docs/verification/2026-09-26-visible-source-capture.md` with the exact commit tested, commands used, automated test summary, eight-platform matrix, known limitations, and any platform requiring user login or follow-up. Do not embed private conversation text or raw HTML.

- [ ] **Step 6: Run both local caller smoke tests**

From the Codex connector instructions and then the WorkBuddy connector instructions, execute a single manual DeepSeek probe and confirm both call the same repository-local CLI and return the new source fields. These are two entry-point checks over one local implementation, not separate installations.

- [ ] **Step 7: Commit evidence**

```bash
git add docs/verification/2026-09-26-visible-source-capture.md
git commit -m "test: verify visible source capture across platforms"
```

## Task 9: Final review, backup, and release handoff

**Files:**

- Modify if needed: `README.md` only if it exists by implementation time
- Modify if needed: `docs/verification/2026-09-26-visible-source-capture.md`

- [ ] **Step 1: Review the branch diff against the approved design**

```bash
git diff --check main...HEAD
git diff --stat main...HEAD
git log --oneline --decorate main..HEAD
```

Verify explicitly that no source-status path changes a successful answer status, no platform is `unsupported`, no secrets or raw page dumps are tracked, and the V1 tag still resolves to `dbf61ba`.

- [ ] **Step 2: Run final verification from a clean process**

```bash
pytest -q
python scripts/doctor.py --json
git status --short
```

Expected: all tests pass, doctor output is recorded in the verification report, and the tree contains only intentional documentation updates if any.

- [ ] **Step 3: Request an independent code review**

Use `superpowers:requesting-code-review` with the approved design, this plan, the branch diff, and the verification report. Resolve findings test-first; rerun the full suite after every production fix.

- [ ] **Step 4: Push a recoverable implementation branch**

```bash
git push -u origin codex/visible-source-capture
```

Do not move `look4geo-probe-v1`. After user acceptance, create a new immutable release tag such as `look4geo-probe-v1.1-visible-sources` on the verified commit and push that tag.

- [ ] **Step 5: Hand off exact outcomes**

Report:

- the tested commit and branch;
- which of the eight platforms passed real answer and source extraction;
- which platforms were blocked by login/rate limit/timeout and therefore remain unverified;
- the locations of the verification report and local result artifacts;
- the unchanged CLI examples for manual and multi-platform routing;
- the V1 rollback tag and the new release tag, if accepted.

