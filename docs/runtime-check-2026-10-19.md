# Local runtime investigation — 2026-10-19

Latest local failures at 15:41–15:42 Asia/Shanghai:

- ChatGPT: `message textbox not found for chatgpt`.
- Gemini/Camoufox: `Gemini composer is unavailable`, previously classified as login required without a visible login wall.

The configured ChatGPT BrowserSkill instance 81483588 was connected. A fresh
driver process and the shared `scripts/probe-local` launcher both completed real
diagnostic questions. Launcher job `4580c9f8-eb37-4d61-ad95-977a10b53052`
returned `Look4GEO CLI check OK`, with English locale verified. These short
diagnostics do not validate procurement source capture or long batches.

Multiple persistent MCP processes predated recent source updates; stale loaded
code remains a possible explanation for caller differences, not a proven root
cause. Reconnect an idle client to load current code. No shared daemon or MCP
process was terminated during this investigation.

The configured Gemini Camoufox profile rendered a native
`textarea[placeholder="Ask Gemini"]`, with no rich-text composer or mode picker.
Added support for that textarea and verification through its input value.
Missing composers without a login/verification wall now produce extraction
failure with screenshot/HTML evidence rather than an unsupported login diagnosis.

Initial launcher verification job `d51c023a-1026-4a8a-b233-7a1f532565f7`
reached the Flash-mode guard and stopped with
`Gemini 3.8 Flash mode could not be confirmed`. No question was sent without
confirmation.

After the user confirmed the same profile showed Flash, a timed read-only
reproduction found the actual startup sequence: at 0–45 seconds the page showed
only placeholder content and no buttons; around 60 seconds it became complete,
including `Open mode picker, currently Flash`. No page script errors occurred.
The former 15-second mode wait exited before hydration completed. Raised this
wait to 90 seconds, still bounded by the overall caller timeout and still
requiring explicit Flash confirmation. No account or route was changed.

Final shared-launcher Gemini verification succeeded: job
`3c2e366b-bcf8-4619-bde8-bcae1f18880b`, explicit Flash guard passed,
nonempty English answer extracted, no failure or diagnostic. Runtime was
approximately 109 seconds including browser startup and page hydration.
This explanatory question exposed no sources, so citation capture remains
unverified by this particular check. This is one answer, not an R=3 batch.

Verification after the readiness change: 361 tests passed; whitespace check
passed. Browser debug sessions were closed. The manual Gemini window was closed
gracefully before reusing its profile. Customer batches and their saved results
were not resumed or edited. Local source changes require an idle persistent MCP
client to reconnect before it can load this version.
