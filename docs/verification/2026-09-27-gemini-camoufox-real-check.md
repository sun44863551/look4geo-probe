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

### R=4 stability

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

## Accounting and conclusion

Login-required, timeout, send-failed, and extraction-failed samples are
collection failures. They are excluded from mention and citation denominators
and must never be encoded as mention `0`, citation `0`, or "not mentioned".
Fallback answers may be retained as separately identified observations, but do
not repair a failed native-adapter stability sample.

**Conclusion: intermittent.** Gemini is usable through the private unified
Look4GEO Probe and demonstrated real answer and citation extraction, including
R=1 and R=2 success. It did not meet the strict native `4/4` stability gate:
two R=4 samples timed out in the answer-completion phase and were satisfied only
by the configured fallback chain.
