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

All browser credentials remain in the user's Chrome profile. Probe jobs and
results remain in the local SQLite database under `data/`.
