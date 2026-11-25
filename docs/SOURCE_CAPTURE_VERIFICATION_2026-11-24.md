# Browser source capture verification — 2026-11-24

## Repairs

ChatGPT grouped citation triggers can remain outside the viewport after an in-page scroll command. The adapter now uses BrowserSkill native scrolling and verifies hit testing before opening the group. It opens the group and source cards with targeted keyboard actions. Opening a website can dismiss the parent popover, so every card is rebound against the live panel and its title is verified. A selector missing before dispatch permits a bounded refresh. Interrupted or uncertain actions preserve the partial-capture status.

The earlier fixes for Gemini Auto, Google account slots, fresh citation references, and private source evidence remain included. This machine uses browser 81483588. Gemini uses account slot 1 and Auto.

## Real web verification

| Platform | Sample | Answer characters | Website source URLs | Capture status |
|---|---|---:|---:|---|
| ChatGPT | R1 | 5584 | 16 | captured |
| ChatGPT | R2 | 6044 | 17 | captured |
| ChatGPT | R3 | 5408 | 15 | captured |
| Gemini | R1 | 1377 | 2 | captured |
| Gemini | R2 | 1803 | 3 | captured |
| Gemini | R3 | 2031 | 4 | captured |
| Perplexity | R1 | 3925 | 11 | captured |
| Perplexity | R2 | 4193 | 9 | captured |
| Perplexity | R3 | 4066 | 9 | captured |

ChatGPT's three runs include websites recovered from grouped source panels, as well as answer links. Gemini initially returned Something went wrong (1) in all three attempts. After the user confirmed manual answering worked, the CLI short test returned GEMINI WEB OK, and a fresh source-bearing R=3 passed. The exact change that restored Gemini availability was not established.

All nine final samples have a nonempty answer, website source records and captured status with no capture diagnostic. ChatGPT and Gemini also have private evidence files. Source counts use stored URLs, which may include different observed query parameters on the same page.

The complete regression suite passed with 439 tests. The repaired code is local and awaits a separate Git synchronization request. Historical customer records have their own recovery audit and are not established as complete by these new tests.
