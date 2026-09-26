# Look4GEO Probe Visible Source Capture Design

**Date:** 2026-09-26

## Goal

Extend the existing private Look4GEO Probe so every supported platform can collect the browser-observable sources for one answer in two categories:

1. `cited`: a source explicitly linked from the answer body.
2. `surfaced`: a source shown in the platform's source panel or search-result cards but not linked from the answer body.

The supported platforms are Doubao, DeepSeek, Yuanbao, Qwen, ChatGPT, Gemini, Perplexity, and Grok. Network-response candidates and server-side hidden retrieval data are explicitly out of scope for this phase.

## Constraints

- Keep the probe local and private. Codex and WorkBuddy continue to call the same local CLI.
- Reuse BrowserSkill and the existing adapter pipeline; do not create a separate scraper system.
- Preserve the existing `citations` field for compatibility.
- Do not treat source-capture failure as answer failure.
- Do not infer hidden sources. Record only evidence visible in the answer DOM or a platform source panel.
- Do not navigate to external source pages. The collector may open a platform-owned source panel or card drawer.
- Do not count platform navigation, ads, suggested follow-up questions, or internal chat links as sources.

## Terminology and Truth Boundary

"All sources" means all sources observable in the browser for the current answer after expanding the platform's source UI. It does not include candidates retained only on the platform server.

Every source must carry evidence showing where it was observed. A result with no exposed sources is valid and must not be converted into a collection failure.

## Data Model

Add the following models:

```python
class SourceRole(StrEnum):
    CITED = "cited"
    SURFACED = "surfaced"


class SourceEvidenceOrigin(StrEnum):
    ANSWER_DOM = "answer_dom"
    SOURCE_PANEL = "source_panel"


class SourceCaptureStatus(StrEnum):
    CAPTURED = "captured"
    NONE_EXPOSED = "none_exposed"
    UNSUPPORTED = "unsupported"
    FAILED = "failed"


class SourceRecord(StrictModel):
    url: str
    title: str | None = None
    domain: str
    snippet: str | None = None
    source_role: SourceRole
    evidence_origin: SourceEvidenceOrigin
    linked_in_answer: bool
```

Extend `PlatformAttempt` with:

```python
sources: list[SourceRecord] = Field(default_factory=list)
source_capture_status: SourceCaptureStatus = SourceCaptureStatus.NONE_EXPOSED
source_capture_diagnostic: str | None = None
```

The existing `citations` field remains. For BrowserSkill results it is derived from the normalized `sources` records whose role is `cited`. Existing consumers can continue reading `citations`; new consumers can use `sources`.

The output schema version increases from 1 to 2. Stored schema-version-1 results remain readable because the new fields have defaults and the schema version field accepts the stored value.

## Collection Architecture

### Platform configuration

Each platform entry in `PLATFORMS` gains source-capture configuration:

- selectors that bound the current assistant answer;
- selectors or visible labels for opening a platform-owned source panel;
- selectors that identify source cards inside the panel;
- internal domains and URL patterns to exclude.

Platform-specific configuration is data. Shared extraction, normalization, classification, and deduplication remain generic code.

### Collection sequence

For each successful answer:

1. Wait until the answer is stable using the existing completion state machine.
2. Collect external links inside the selected answer boundary and classify them as `cited` / `answer_dom`.
3. Find the platform's source-panel trigger within the current answer context.
4. If a trigger exists, open it once and wait for the panel to stabilize.
5. Extract external URLs, visible titles, and visible snippets from source cards and classify them as `surfaced` / `source_panel`.
6. Normalize URLs, remove excluded links, and deduplicate.
7. Promote any duplicate `surfaced` record to `cited` when the same normalized URL occurs in the answer.
8. Populate legacy `citations` from the final cited subset.
9. Close the owned browser session through the existing lifecycle.

Opening the source panel is an inspection action. The collector must not click a source card or navigate to the external page.

## URL Normalization and Deduplication

Normalize before comparison:

- require `http` or `https`;
- lowercase the hostname;
- remove URL fragments;
- remove known tracking parameters such as `utm_*`, `gclid`, and `fbclid`;
- preserve query parameters that identify actual content;
- unwrap platform redirect URLs only when the destination is present as a normal URL parameter;
- reject the platform's own chat, account, navigation, and asset URLs.

Deduplication uses the normalized URL. If title or snippet differs, retain the first non-empty value. `cited` outranks `surfaced`, and `answer_dom` outranks `source_panel` for the evidence origin of a cited record.

## Capture Status Semantics

- `captured`: at least one valid `cited` or `surfaced` source was collected.
- `none_exposed`: answer succeeded, collection ran, but the browser exposed no valid sources.
- `unsupported`: the platform configuration intentionally has no safe visible-source rule. This is acceptable only during staged rollout and is not acceptable for final completion of the eight-platform task.
- `failed`: the answer succeeded but source inspection failed because of stale selectors, panel errors, or extraction errors.

`failed` and `unsupported` must include a diagnostic. Source status never changes a successful answer to failed, and failed attempts are never counted as “not mentioned.”

## Platform Rollout

Implement and verify all eight platforms in this phase. Configuration is informed by real pages, but the shared collector is implemented first.

For each platform, one search-enabled real prompt must verify:

- answer extraction still succeeds;
- answer-body citations are classified as `cited`;
- visible source cards not present in the answer are classified as `surfaced`;
- duplicates are merged with `cited` precedence;
- a source-free answer becomes `none_exposed`, not `failed`;
- panel failure does not erase the answer.

Qwen's existing asynchronous/slow-search limitation remains separate. A timeout cannot be reported as source-capture success. If Qwen completes, its sources are tested normally; if it does not complete during a run, the run remains an answer timeout and is not used to validate source extraction.

## Storage and Compatibility

`ProbeStore` already stores complete result JSON, so no SQL table migration is required. New fields are serialized inside `result_json`.

Compatibility rules:

- `citations` remains present and keeps its current shape.
- schema-version-1 stored records load with empty `sources` and default source status.
- Codex and WorkBuddy continue using `scripts/probe-local` with no command change.
- CSV/report exporters added later can distinguish cited and surfaced sources without reparsing raw answers.

## Error Handling

- A missing panel trigger is not automatically an error; it yields `none_exposed` if no cited links exist.
- A trigger that is found but cannot be opened yields `failed` with a platform-specific diagnostic.
- A panel that opens but contains no valid external source yields `none_exposed`.
- Stale element references are re-observed once; repeated failure is recorded and not retried indefinitely.
- Login, rate limit, send, answer extraction, and timeout behavior remain unchanged.

## Privacy and Evidence

This phase reads only page DOM already visible to the logged-in browser. It does not retain cookies, authorization headers, or raw network payloads. Source records contain public URLs and visible source metadata only.

Raw page HTML is not stored by default. If debugging artifacts are needed, they remain under the existing local per-run artifact directory and are never committed.

## Testing Strategy

### Unit tests

- URL normalization and redirect unwrapping.
- platform-internal and non-HTTP URL rejection.
- cited/surfaced classification.
- cited precedence during deduplication.
- title/snippet merge behavior.
- source-capture status calculation.
- legacy citation derivation.
- schema-version-1 result compatibility.
- panel failure preserving a successful answer.

Every production behavior is introduced through a failing test first.

### Adapter tests

Use realistic DOM-derived fixtures for each platform. Tests assert observable `SourceRecord` output rather than selector text.

### Full regression

Run the complete Python test suite and the existing validity gate.

### Real browser verification

Run one search-enabled prompt per platform in isolated BrowserSkill sessions. Record answer status, cited count, surfaced count, total deduplicated sources, source-capture status, and diagnostic. A platform passes when the collector accurately reflects what its page exposes; a zero count is acceptable only when verified as `none_exposed`.

## Completion Criteria

The phase is complete when:

- all automated tests pass;
- all eight platforms have a real test record;
- no platform remains `unsupported`;
- successful answers distinguish `captured`, `none_exposed`, and `failed` honestly;
- legacy `citations` output remains compatible;
- Codex and WorkBuddy can call the unchanged local CLI and receive the new fields;
- the implementation and test evidence are backed up to Git.

## Out of Scope

- Browser network-response capture.
- Hidden server-side retrieval candidates.
- Visiting or scraping the external source pages.
- Independent scraper services or cloud APIs.
- Ranking source quality or attributing individual claims to individual sources.
- Async answer recovery for Qwen.
