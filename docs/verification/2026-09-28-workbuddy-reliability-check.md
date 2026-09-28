# WorkBuddy reliability verification — 2026-09-28

## Scope

Private local `scripts/probe-local` execution shared by Codex and WorkBuddy.
No public WorkBuddy skill or cloud connector was used.

## Environment

`scripts/probe-local doctor --json` passed every required check: Python, Node,
Git, BrowserSkill, Chrome, AI-Search-Hub, Promptfoo, and Camoufox. Docker is
optional and absent.

## Real platform checks

- Gemini: succeeded through `camoufox_gemini`, job
  `5e2159a8-6f7b-4161-adc0-87b6eff5885d`, answer `GEMINI_SMOKE_928`.
  This confirms the scoped WorkBuddy sandbox configuration launches and extracts.
- Perplexity: succeeded through `browser_skill`, job
  `f47fb10f-aaf3-41bd-9dcc-ad49536e0503`, answer
  `Look4GEO Perplexity OK`. The live page showed `Search` / `搜索` selected
  (`aria-pressed=true`) and `Computer` unselected. The adapter now confirms
  ordinary search before every send and collects only links in the current
  answer DOM.
- Grok: input and native keyboard submission were confirmed against the live
  page, but the account returned `距离限制重置还剩 1小时 13分钟` and
  `Upgrade to SuperGrok`. This is an external platform quota, not a probe
  extraction result. The adapter now classifies this wall as `rate_limited`
  instead of waiting until timeout. A successful-answer rerun remains pending
  until the platform limit resets.

The first Gemini smoke used a prompt containing the reserved contamination term
`Look4GEO`; the system correctly rejected that result. The neutral rerun above
is the valid verification.

## Perplexity quota policy

Perplexity's current official Free-plan documentation says basic searches are
practically unlimited and lists Pro Search as 3/day. It does not publish a
guaranteed daily reset clock. This deployment does not intentionally use Pro
Search. If a real quota wall is still returned, the WorkBuddy batch records
`suspended_quota`, skips later Perplexity cells, checks no earlier than the next
day, and resumes only after a real ordinary-search probe succeeds.

Official reference:
https://www.perplexity.ai/help-center/en/articles/11187416-which-perplexity-subscription-plan-is-right-for-you

## Safety

Grok no longer falls back automatically to AI-Search-Hub because that vendor
path attempts large debug-profile cleanup under WorkBuddy. No broad deletion
permission was granted. Other platform chains are unchanged.
