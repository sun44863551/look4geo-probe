# Gemini Camoufox Real-Browser Check — 2026-09-28

## Scope and environment

- Private local Look4GEO Probe checkout on the Mac mini.
- Preferred Gemini adapter: `camoufox_gemini`; BrowserSkill and
  AI-Search-Hub remained configured fallbacks.
- Python package: Camoufox `0.5.6`.
- Browser build: `official/stable/152.0.4-beta.30`.
- Browser storage: approximately `639.1 MB` under
  `data/camoufox/cache`.
- Persistent login profile: `data/camoufox/profiles/gemini` (ignored by Git;
  never upload).
- Promptfoo `0.123.1` was reused from the main private checkout for the
  isolated-worktree doctor check.
- Required doctor checks passed. Docker was absent but optional.

## Login and defects found during acceptance

The headed login flow initially treated Gemini's anonymous composer as an
authenticated composer and closed too soon. The adapter was changed to reject
the visible `Sign in to save activity` state and to keep the window open for up
to five minutes. A second issue was that the authenticated composer could load
after navigation; the adapter now waits for it. The user then completed login
in the visible window and the command returned `succeeded`.

The first send attempt exposed a timing problem around the Send button. The
adapter now uses a bounded click and falls back to pressing Enter in the
composer, while still verifying that submission changed the page state. These
fixes are covered by automated tests.

The planned smoke phrase contained `Look4GEO`, which the existing contamination
guard intentionally rejects. The equivalent phrase `Gemini Camoufox adapter
OK` was used instead; the safety rule was not weakened.

## Real runs

### R=1 smoke

- Job: `70efc62a-099c-4dab-8fe4-ef6207fb1ee1`
- Adapter: `camoufox_gemini`
- Status: `succeeded`
- Answer: `Gemini Camoufox adapter OK`
- Source status: `none_exposed` (expected for an exact-phrase request)

An earlier smoke job, `ca20b7fe-268c-4f67-8845-01b6727810de`, was rejected by
the contamination guard because its requested answer contained `Look4GEO`.
That failure is not a business "not mentioned" observation.

### R=2 answer and source boundary

- Job: `d3cae627-8f52-416a-95c4-17205e5a79ce`
- Question: Python 3.12.0 official release date, with visible web sources.
- Native result: `2/2` succeeded with `camoufox_gemini`.
- Both answers reported October 2, 2023.
- Sample 1 captured two cited source links.
- Sample 2 captured three cited source links.
- Both samples used `source_capture_status: captured`; links came from the
  answer DOM and were not inferred from model internals.

### Initial R=4 stability run

- Job: `07ff136d-d7d5-456c-b1bc-23de8366e265`
- Same neutral source question as R=2.
- Sample 1: `camoufox_gemini`, succeeded, sources captured.
- Sample 2: `camoufox_gemini` timed out after 120 seconds; the unified tool then
  returned an AI-Search-Hub fallback answer.
- Sample 3: `camoufox_gemini`, succeeded, sources captured.
- Sample 4: `camoufox_gemini` timed out after 120 seconds; the unified tool then
  returned an AI-Search-Hub fallback answer.

The job-level status was `succeeded` because fallback answers were available,
but the native Camoufox acceptance result was only `2/4`. Fallback success must
not be represented as native stability.

### Post-fix strict R=4

Investigation reproduced a silent Gemini submission failure: the composer could
clear without creating a conversation, showing a generation control, or
producing an answer. The old adapter treated composer clearing alone as send
success and then waited until the 120-second answer timeout. Submission now
requires a verified conversation URL, answer region, conversation structure,
or generation-state change. An unverified clear causes the prompt to be filled
again and retried, up to three sends.

A subsequent run also exposed Gemini's transient response, `I seem to be
encountering an error`. This platform error is no longer accepted as a business
answer; the native adapter opens a new conversation and retries up to three
times before returning `extraction_failed`.

- Job: `ea81c59f-2f1d-416b-b249-3262b23396e1`
- Question: same neutral Python 3.12.0 source question.
- Native result: `4/4` succeeded with `camoufox_gemini`.
- No BrowserSkill or AI-Search-Hub fallback was used.
- All four answers reported October 2, 2023.
- All four samples used `source_capture_status: captured` and contained visible
  cited links from the answer DOM.
- Per-sample completion time was approximately 11–14 seconds.

## Accounting and conclusion

Login-required, timeout, send-failed, and extraction-failed samples are
collection failures. They are excluded from mention and citation denominators
and must never be encoded as mention `0`, citation `0`, or "not mentioned".
Fallback answers may be retained as separately identified observations, but do
not repair a failed native-adapter stability sample.

**Final conclusion: stable for this acceptance run.** Gemini is usable through
the private unified Look4GEO Probe and demonstrated real answer and citation
extraction for R=1, R=2, and a post-fix strict native R=4. The final R=4 met the
`4/4` gate without fallback. This is evidence for the tested machine, account,
question, and date; future platform UI changes or rate limits must still be
reported as collection failures rather than converted to negative business
observations.
