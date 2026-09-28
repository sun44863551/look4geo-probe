# WorkBuddy local integration

This directory is for a private, machine-local WorkBuddy integration. It is
not a marketplace connector package and must not be uploaded to the public
Experts, Skills, or Connectors catalog.

The primary integration has one local piece:

1. Copy `skills/look4geo-probe/SKILL.md` to
   `~/.workbuddy/skills/look4geo-probe/SKILL.md`.
   The skill calls the checkout's `.venv/bin/probe` command directly through
   WorkBuddy's local terminal capability. No Connector import is needed.

`mcp.json` is an optional private stdio configuration for a future WorkBuddy
version that exposes local MCP registration. It is not an importable catalog
package. The CLI and MCP interfaces both use the same local service and data.

The checked-in paths are specific to this Mac mini deployment. If the project
directory or BrowserSkill browser ID changes, update `mcp.json` locally.

Both Codex and WorkBuddy must call the repository-local
`scripts/probe-local` entrypoint (or the optional local stdio MCP server). They
must not copy the implementation into a public WorkBuddy skill area. Manual
platform selection remains `--mode manual` with one or more repeated
`--platform <name>` flags.

Active platform IDs are `doubao`, `deepseek`, `yuanbao`, `baidu`, `chatgpt`,
`gemini`, `perplexity`, and `grok`. Baidu remains inside this same unified
local tool; Qwen is no longer an active platform.

Gemini is also not a separate tool. The unified probe prefers
`camoufox_gemini`, then falls back to BrowserSkill and AI-Search-Hub when the
failure is eligible for fallback. Its persistent profile lives at
`data/camoufox/profiles/gemini`; this directory is machine-local, contains
login state, and must not be uploaded. Start first-time login with
`scripts/probe-local login gemini --json`, complete any Google challenge only
in the visible browser window, and continue using the normal `run` command.
`login_required` means the sample was not collected; it must never be recorded
as "not mentioned" or included in measurement denominators.

On this Mac mini, WorkBuddy runs Camoufox inside its own outer sandbox. If
Firefox fails with `sandbox_init: Operation not permitted`, set
`LOOK4GEO_CAMOUFOX_DISABLE_CONTENT_SANDBOX=1` in the private ignored
`config/local.env`. The launcher applies `MOZ_DISABLE_CONTENT_SANDBOX=1` only
while Camoufox is running and restores the previous process environment. Do
not export it globally and do not use it on an untrusted host.

Results keep answer status separate from source capture. `captured` means at
least one visibly exposed source was collected, `none_exposed` means the UI
showed none, and `failed` carries a `source_capture_diagnostic` without turning
a successful answer into a failed answer. Source roles distinguish links
`cited` in the answer from links merely `surfaced` in a visible source panel.

All browser credentials remain in the user's Chrome profile. Probe jobs and
results remain in the local SQLite database under `data/`.
