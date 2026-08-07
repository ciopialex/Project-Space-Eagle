# Aethelark — state of play and what's next

Written 2026-08-07, at the end of a long session. Everything here is either
measured or verified live; where something is unproven it says so. The point
of this document is that the next session should not have to rediscover any
of it.

---

## 1. What works right now

| capability | state | evidence |
|---|---|---|
| **YouTube — liked / playlists / subscriptions** | working | live: `via: api`, ~200ms |
| **Web perception** (any site) | working | 98.3% control coverage across 6 sites |
| **Web action** (click, type, forms) | working | consent walls auto-declined, refuses irreversible clicks |
| **Sign-in to any site** | working, unproven end-to-end | window opens, session sticks; never watched a human complete it |
| **Voice** | working | −250ms/turn from the VAD window |
| **Tests** | 1546 passing | from 622 at the start of the week |

### The one thing a user must do once
Ask for something on a site → the eagle opens a window → they sign in →
it verifies and closes. Once per site, forever. That is the **only** way in;
two others were built and deleted (see §4).

---

## 2. Open work, in priority order

### P1 — Finish the tool contract rollout
**5 of 20 tools** use `ToolResult`. The rest return bare strings.

`normalize()` no longer invents `ok=True` for them, so nothing lies any more —
but they also give the model no status and no guidance. Migrating the rest is
the highest-value systematic work left.

Queue, by failure-shaped returns that still reach the model with no status:
`game_updater` (15), `computer_control` (15), `browser_control` (15),
`file_controller` (15), `swarm_orchestrator` (14), `code_helper` (11),
`desktop` (10), `dev_agent` (10).

**Migrate at the boundary**, as done for `file_processor`: the entrypoint knows
what it decided, internal helpers keep their prose. 123 call sites is churn;
the boundary is where the value is.

### P2 — Live voice baseline (blocked on the user)
The trace is wired and inert behind `AETHELARK_TRACE=1`. **Nobody has ever
spoken to it with tracing on**, so there is no baseline. The user reported a
regression from ~0.8s to ~4s; three real causes were found and fixed by
inspection (Chrome left running, +23% prompt/tool context, the 550ms VAD
window) and none of them plausibly accounts for 5×.

One `[Trace]` line settles it: `to_voice` / `response` / `audio` / `spoken`
splits model time from playback time from reply length.

Also worth checking with zero instrumentation: **does a `🔧` line appear in the
terminal when the user says "how are you doing"?** If a tool fires on a
greeting, that is seconds right there and it is a routing bug.

### P3 — Video summarisation
Wanted, and currently impossible. Three routes tried and all failed:
the timedtext endpoint returns empty to any HTTP client; the same fetch from
*inside* the page needs a signed origin token now; clicking "Show transcript"
leaves the panel unpopulated. Best hypothesis: it needs a **signed-in**
browser, which is now achievable. Retry after a real sign-in before writing
any code.

### P4 — Spotify, then GitHub
Both have clean APIs and the same OAuth shape as Google. Spotify covers
playlists, saved tracks, recently-played *and playback control*. GitHub
unlocks the "update my CV from my repos" task. Neither started.

### P5 — `computer_control` / `computer_settings` timing
`_type`'s 300ms and `_clear_field`'s 100ms remain. Unlike focus and clipboard
they have **no observable condition**, so they need a measurement harness
before anyone touches them. Changing them blind is guessing.

---

## 3. Parked designs (documented, not built)

`docs/Aethelark_Responsiveness_Notes.md` holds two, with the reasoning:

- **Conversational backchannels** ("checking", "mhm", "one sec"). The
  constraint that shapes it: if the brain already emits its own filler in
  voice mode — GPT-5.5 does — the eagle says "checking" twice. The brain is
  swappable, so this is architecture. Recommendation: a capability flag each
  adapter declares.
- **Predictive preloading** (the Facebook login story). Safe only for
  operations with **no side effects**: warming a browser, navigating, reading
  a DOM. Speculative *navigation* is the feature; speculative *action* is a
  bug with a nice name.

---

## 4. Hard constraints — do not re-litigate these

- **A Google session cannot be copied into another browser profile.** Chrome
  binds it to the profile that created it. Verified: every cookie transferred
  and decrypted, the browser loaded `SID`/`SAPISID`/`__Secure-1PSID`, and
  Google reported signed out anyway and deleted `LOGIN_INFO`. This is
  session-theft protection working. The Chrome-import subsystem (~1000 lines)
  was deleted because of it.
- **Watch history has no API.** Google removed it in 2016. Only the page works.
- **Playwright launches Chrome with `--password-store=basic`**, which cannot
  decrypt Chrome's keyring-encrypted (`v11`) cookies. Fixed with
  `--password-store=gnome-libsecret` on Linux; remember it if the browser ever
  appears mysteriously signed out.
- **`web/dashboard.html` is generated** by `web/build_app_ui.py`. Editing the
  output works until the next rebuild. Its bridge JS lives inside a Python
  string, so escapes get consumed once on the way out.
- **Qt: never touch the web view from a worker thread.** Use
  `_push_settings_async` / `write_log`, which emit signals. A direct `_push`
  from a thread crashes the app; there is now a test that walks the AST to
  enforce it.

---

## 5. Lessons that cost the most to learn

**Run the user's actual sentence.** Every time it was run end to end it broke
in one attempt. Every time it was measured at a seam of the assistant's own
choosing, the numbers improved and the feature stayed broken. Seven separate
bugs sat in a row on "tell me my latest liked video", each hiding the next.

**A fixed delay standing in for a measurement is this codebase's most common
bug.** Found and fixed in three places: the page settle (1200ms → adaptive,
−990ms/navigation), the consent wall (500×6 → adaptive, 4001ms → 1001ms), and
`focus_window` (300ms → 3ms). `computer_control` still has two, deliberately
left because there is nothing to measure yet.

**Tools that report success they never verified** are the second most common.
`focus_window` claimed focus it never checked; `_clipboard_paste` pasted the
previous clipboard; `desktop_control` turned a typo into `exec()`. The
`ToolResult` contract exists for exactly this and is only 25% rolled out.

**String surgery without re-reading the result.** Four defects in one session
came from regex/slice edits that were never looked at: a corrupted sentence in
the live prompt, a duplicated function block, an unreachable exception handler,
a stale parameter. Not thinking errors — not-looking errors.

**A test that passes before and after the fix proves nothing.** Happened twice
(the profile-import guard, an early collector test). Reconstruct the pre-fix
state and confirm the test fails against it.

---

## 6. Housekeeping

- `origin/main` is current. 74+ commits pushed this week.
- Personal identifiers scrubbed from tracked files, with a guard test.
  **Still in git history:** `config/certs/aethelark.key`, a self-signed LAN
  cert, from before this work. Not in the tree, not on disk, nothing presents
  it. Rewriting history would not un-publish it — just never restore that key.
- **No LICENSE file.** A public repo with no licence is "all rights reserved",
  which blocks commercial use — including the owner's. Two other contributors
  hold copyright in ~25% of the surviving code by blame. One message asking
  them to agree to MIT/Apache-2.0 closes this permanently.
- `~/.local/bin/eagle-dev` is redundant now that everything is merged.
