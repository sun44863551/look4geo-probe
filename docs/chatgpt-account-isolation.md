# ChatGPT sampling account isolation

ChatGPT requires an explicitly configured probe Chrome profile and BrowserSkill
extension instance. The probe reads `LOOK4GEO_CHATGPT_BROWSER_ID`; an empty ID
disables ChatGPT sampling. It never falls back to an unconfigured or daily
browser. When the whole probe uses one isolated Chrome profile, this ID may
equal `LOOK4GEO_BROWSER_ID`. Codex and WorkBuddy use the same routing policy.

1. Create a new Chrome profile named `Look4GEO Sampling`. Keep Chrome sync off;
   do not copy cookies or import the daily profile's browsing data.
2. Install BrowserSkill in that profile and connect it to the local daemon.
3. Open `https://chatgpt.com/` there and sign in with the sampling account.
   If using Google login, select the sampling Google account in this profile.
4. Open `chrome://version` to verify the dedicated Profile Path. In the
   BrowserSkill popup, use **Copy profile instructions** to identify the instance.
5. Put the verified instance ID in the ignored `config/local.env`:
   `LOOK4GEO_BROWSER_ID=<probe-instance-id>` and
   `LOOK4GEO_CHATGPT_BROWSER_ID=<probe-instance-id>`.
6. Call the local `scripts/probe-local` entrypoint from either Codex or WorkBuddy.
   If calling the MCP server directly, supply the dedicated variable to its
   environment and restart that server.

Close or disconnect the dedicated profile when sampling is finished. Failure
to connect to this instance cannot switch the probe to the daily browser.
Browser isolation separates cookies, account sessions, history and quotas;
it does not separate the computer's network address or operating-system access.
Use a genuinely different ChatGPT account; logging the daily account into the
sampling profile would still consume the daily account's quota.

## English-language baseline

For each ChatGPT probe session, the adapter applies `Accept-Language: en-US,en`
to its own BrowserSkill tab before loading ChatGPT, preserving the tab's actual
User-Agent. It then checks both the browser and page language before sending a
prompt. This does not change the Chrome profile or other platforms' tabs.

An older answer written in English while its source labels were localized in
Chinese is **content-only evidence**, not an English-interface buyer baseline.
The locale fix is not retroactive: collect fresh samples under the new probe
version and keep the original answers, citations and capture dates for audit.
If the English page check fails, the attempt fails rather than silently
falling back to a Chinese-interface sample.
