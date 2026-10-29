# Overseas web verification — 2026-10-27

Scope: ChatGPT, Gemini Flash, and Perplexity ordinary Search; three independent
samples per platform, using English buyer settings. User clarified that all
BrowserSkill routes must use instance `81483588` (Look4GEO Sampling).

## Final verified results

| Platform | Verified samples | Answer characters | Reviewed source URLs excluding example domains |
|---|---|---|---|
| ChatGPT / 81483588 | 3/3 | 2547, 2073, 2298 | 3, 3, 3 |
| Perplexity Search / 81483588, after completion fix | 3/3 | 1965, 2123, 2032 | 4, 3, 3 |
| Gemini Flash / 81483588, after native input/Enter repair | 3/3 | 1975, 2193, 1989 | 6, 4, 3 |

ChatGPT job IDs:
`b8789cc4-b056-4b81-87f6-c71b953f0971`,
`7e548f96-2aec-429d-aa6f-92632cd5b0f6`,
`7fcaa6f9-3293-4002-ae8e-b3522bbffdef`.
All three passed the English browser/page locale check.

Perplexity final job IDs:
`287d6c67-1bb2-44d1-9202-48e04bc3bea0`,
`c37dc7f1-fca8-4026-b94c-c0686c2310c4`,
`0893b09e-3efa-4b13-b472-01f76b31c680`.
The ordinary Search guard passed; no Pro Search was requested.

Gemini final job IDs:
`9160f285-1d94-4e03-a1a8-84256ff20cc0`,
`974a2d98-924b-49f6-a46b-e85f9553fd4c`,
`7c54c1f0-d27a-4eb8-b2e6-9a08a1946ebf`.
All used the shared local launcher, BrowserSkill instance 81483588, and explicit
Flash-mode confirmation. Runtime was 39, 41, and 44 seconds respectively.
All returned captured visible sources and no failure or diagnostic. Source
record counts before excluding illustrative example domains were 7, 6, and 4.

The query requested a 200–350-word comparison of robots.txt and sitemaps with
authoritative sources. Raw URL arrays also contain illustrative example.com
URLs rendered in the answers. Those are not research sources and are excluded
from the reviewed counts above. Raw saved records are preserved unchanged.

## Repairs

1. Perplexity streamed partial text while exposing the actual stop label
   `停止响应（Esc）`. The generation detector missed this label and could accept a
   pause mid-word as a completed answer. Added Chinese stop-response and English
   Stop response labels; executable JavaScript regression tests failed before
   the repair and passed after it.
2. Gemini BrowserSkill checked the Flash picker only once. Added bounded
   readiness polling before accepting or switching the mode. The same instance
   was independently observed logged in with `currently Flash`.
3. The legacy Gemini Camoufox source filter excluded every google.com subdomain,
   dropping Google Search Central and support documentation. Narrowed navigation
   exclusions, retaining documentation URLs. This is a compatibility repair;
   Camoufox is disabled in the current local configuration.

## Gemini investigation and subsequent repair

Before the unified-instance instruction, Camoufox produced two successful
initial samples and a 300-second timeout; a later repeat also stalled at
Searching the web. These results belong to a different browser route and are
excluded from the final 81483588 verification.

On 81483588, the first test failed its Flash readiness check. After repairing
readiness, job `1436a26d-0242-45e8-9400-ebe1176b4019` timed out. A private
bulk-fill/native-key experiment verified prompt text and submission, but did
not recover the answer. A shorter follow-up showed the submitted user question
and subsequently returned a transient platform error. That experimental input
path was not adopted in production.

An in-page human check was requested in an explicitly bound 81483588 session.
The human-check request timed out without confirmation. Re-observation showed
an empty composer on the home page, not a manually completed test. The owned
session was then closed. The user subsequently confirmed manual Gemini replies
were normal, and the investigation resumed on the same bound instance.

A short direct `fill` + Enter test returned a complete answer. The identical
original long question then succeeded with direct `fill` + Enter, yielding
2205 answer characters and seven visible source records. This established a
working native browser path, rather than assuming the account was unavailable.
Production entry now fills the prompt in one native command, verifies its text,
and submits with Enter first. A fresh native send-button fallback remains
bounded by submission-state checks; a successful Enter does not trigger another
submission. The old per-character CLI input generated hundreds of calls for
one prompt and consumed much of the operation budget.

Regression tests cover batched input, silent fill rejection, Enter-first
submission, no duplicate send on slow state confirmation, and fresh fallback
controls. Raw failures remain unchanged and excluded from successful counts.

## Local configuration and validation

The ignored `config/local.env` now sets shared, ChatGPT, and Gemini browser IDs
to 81483588; both Camoufox enablement and Camoufox-only routing are off. The
original configuration was copied to the ignored local work directory.
An optional `LOOK4GEO_LOCKED_BROWSER_ID` launcher policy is enabled locally:
stale caller overrides with a different instance fail before executing probe.
Unlocked installations retain their previous explicit-override behavior.
Reconnect idle persistent MCP clients to load current code and environment.

Regression suite: 371 passed. Whitespace check passed. Existing vendor changes
were preserved. Private raw verification files remain under ignored `work/`;
customer batches and historical results were not rewritten. No Git upload was
requested for these changes.
