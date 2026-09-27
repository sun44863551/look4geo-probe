# Look4GEO Probe architecture and Gemini repair verification

Date: 2026-09-27

## Scope

This review covers the local/private Look4GEO Probe that is shared by Codex and WorkBuddy through the same CLI and MCP entry points. It checks repository completeness, reusable upstream projects, dependency reproducibility, and the current Gemini browser route.

## Architecture result

The core repository is structurally complete for the V1 probe workflow:

- unified CLI and MCP entry points;
- manual and automatic platform routing;
- browser and API adapter boundaries;
- job, service, storage, quality, validity, and source extraction layers;
- Codex and WorkBuddy local connectors;
- automated tests.

The audit found one material packaging gap: the runtime declared an AI-Search-Hub fallback under `vendor/AI-Search-Hub`, but the dependency was ignored and was not present in a fresh checkout/worktree. The bootstrap script also cloned an unpinned latest revision. This made fallback availability depend on the individual machine checkout.

## Directly reusable upstream projects

### AI-Search-Hub

AI-Search-Hub remains the smallest compatible fallback for this repository. It exposes lightweight Playwright site scripts and a Gemini adapter. It is now recorded as a Git submodule, pinned to commit `afcc7411335c8b194e309f06af7d189448d0b0e2`, so fresh checkouts receive the same source revision.

Risk: the inspected upstream checkout did not contain a license file. It is retained as an unmodified external submodule rather than copied into Look4GEO source.

### OneGlanse

OneGlanse has useful real-UI Gemini selectors and source extraction behavior, including Gemini response and inline-source selectors. Its complete deployment is substantially heavier: Camoufox plus Node/pnpm, Docker, PostgreSQL, ClickHouse, Redis, and an analysis-model API key. Importing the full project is therefore not a small V1 supplement.

The selector knowledge was used to harden the existing adapter without creating a second user-facing tool.

### Other candidates

- open-geo uses a supervised real-browser approach, but is not a drop-in local adapter for the current CLI contract.
- Promptfoo is appropriate for API evaluation, not for measuring the real Gemini consumer web interface.
- Hosted Gemini scraping services would weaken the local/private requirement and were not adopted.

## Repairs implemented

- Added and pinned the AI-Search-Hub Git submodule.
- Changed bootstrap to initialize the pinned submodule instead of cloning an unpinned head.
- Updated Gemini composer and answer selectors from current real-UI implementations.
- Added fresh-page button discovery and submission-state verification with bounded retries.
- Treated a BrowserSkill fill error as recoverable only when the live composer contains the exact requested prompt.
- Added Google unusual-traffic detection.
- Rejected Gemini landing greetings so they cannot be recorded as probe answers.
- Added regression coverage for all of the above.

## Real-browser evidence

One real Gemini sample succeeded after the selector/send changes:

- job `3c2a18f7-b679-43ca-baac-0621a0a0004d`;
- BrowserSkill answer extraction succeeded;
- quality checks passed;
- five sources were captured.

Repeated runs were not reliable:

- job `629e972e-07a2-407c-9a33-0f40ec472f37`: one BrowserSkill sample succeeded; other samples failed or received a landing greeting;
- job `398d5885-4a34-42fd-85fa-7976d2467775`: browser submission failed and fallback returned landing greetings;
- job `ca877a21-6a6e-444e-8ca2-7f9f7dcd0907`: failed; the false fallback answer was correctly rejected;
- job `7e66c079-d315-4fb9-9552-0a26649ffcbf`: prompt entry was verified, but three send attempts produced no valid conversation transition; the fallback greeting was correctly rejected.

During direct browser diagnosis, a send action reached Google's unusual-traffic interstitial and returned to the Gemini landing page. Debug evidence is retained locally at `/private/tmp/look4geo-gemini-timeout-root-cause.json` for this machine session.

## Verification status

- Automated suite: 170 tests passed.
- Repository whitespace/diff validation: passed.
- Gemini false-success prevention: passed.
- Gemini reliable real-browser R=4: **not passed**.

Gemini is therefore classified as intermittent/blocked by the current Google anti-automation path, not production-ready. Further selector-only changes are not justified by the evidence. The next architecture decision is either to keep the lightweight BrowserSkill route with explicit challenge/user-action status, or approve a heavier Camoufox-based adapter inspired by OneGlanse while preserving the same unified Look4GEO CLI.

## Remaining deployment risk

The deployed checkout contains Promptfoo, but Node is not currently available on the shell PATH. Promptfoo cannot be considered healthy until Node is installed or its executable path is explicitly configured. This does not prevent the Python/browser probe core from running.
