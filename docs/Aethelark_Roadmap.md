# Aethelark — state of play and what's next

Written 2026-08-07; §1 and §2 updated 2026-08-10. Everything here is either
measured or verified live; where something is unproven it says so. The point
of this document is that the next session should not have to rediscover any
of it.

---

## 0. The design principle everything follows

**Make it work on a dumb brain.** The brain is Gemini 2.5 on a free tier —
roughly 15 requests a minute. Every decision moved out of the model and into
code is a decision that stops depending on how clever the model is: routing
precedence, the capability catalogue, the consent gate, the intent decoder,
the tool contract, the vision guard.

That is the bet: when the brain is swapped for a better one, human emulation
gets closer *because the scaffolding already holds*, not because the model
finally guessed right. Anything that only works with a smart brain is not
built yet.

---

## 1. What works right now

| capability | state | evidence |
|---|---|---|
| **YouTube — liked / playlists / subscriptions** | working | live: `via: api`, ~200ms |
| **Web perception** (any site) | working | 98.3% control coverage across 6 sites |
| **Web action** (click, type, forms) | working | consent walls auto-declined, refuses irreversible clicks |
| **Sign-in to any site** | working, unproven end-to-end | window opens, session sticks; never watched a human complete it |
| **Voice** | working | −250ms/turn from the VAD window |
| **Tests** | 1781 passing | from 622 two weeks ago |
| **Downloads** | working | allow-listed; refuses executables and double extensions |
| **Generated desktop code** | contained | 8 escapes measured and closed; see `core/safe_exec.py` |

### The one thing a user must do once
Ask for something on a site → the eagle opens a window → they sign in →
it verifies and closes. Once per site, forever. That is the **only** way in;
two others were built and deleted (see §4).

---

## 2. Open work, in priority order

### DONE 2026-08-10 (third pass — an audit, then the logs)

Two things ran the session: an audit that ran the software instead of reading
it, and the user's own session logs. Almost every defect came from one of
those, not from the queue.

- **The exec sandbox did not contain.** `desktop_control(action="task")`
  generates Python and runs it; the rules against deleting files and calling
  subprocess lived in the PROMPT. Measured against the real sandbox, six
  escapes worked, including invoking `subprocess.Popen(['id'])` reached
  through `().__class__.__bases__[0].__subclasses__()`. `core/safe_exec.py`
  enforces it now — containment through the same gate `file_controller` uses,
  plus an AST check refusing private attributes. All eight exploits re-run
  through the real entrypoint are refused, files on disk verified untouched.
  `pyautogui` is still handed in and that is stated, not hidden.
- **Downloads did not exist.** `accept_downloads` was never set, so Playwright
  cancelled every one — the eagle clicked Download, the DOM reported success,
  no file arrived. "Download a laptop stand from makerworld" could not
  complete. Now a distinct `download` action: a click succeeds when the page
  reacts, a download only when a file is on disk.
- **Page text is now fenced as untrusted.** `5a273af` fed page content to a
  model that can call every tool. Not solvable by detection; solved by
  provenance, plus a download allow-list that refuses executables by default
  and checks every extension component (`laptop_stand.stl.exe`).
- **Bot walls were English-only.** bambulab and makerworld served Cloudflare
  challenges in Romanian — the user's own locale — and both came back
  `ok=True`. The phrase list held the same sentence in English. Keys on
  markers that do not translate now (Ray ID, `cf-chl`, turnstile).
- **The YouTube chain was broken end to end.** `youtube_api` returned titles,
  `summarize` demanded URLs, and the video id was being discarded from the
  API payload that already contained it. Fixed at the source.
- **The intent layer was eating fragments.** `input_transcription` streams
  mid-word; each fragment was stripped then joined with a space, so "Say the
  word ready" became "Sa ve word rea dy" — and that is what `_speculate`
  matched against. The 231ms head start had never once started.
- **The trace was lying.** `first_token` fired on any `server_content`, which
  is also how the USER's transcription arrives — so `response` came out
  negative on 3 of 13 turns and the model's thinking time was being credited
  to our playback path. Fixed, and tracing now defaults ON: it was opt-in for
  weeks and never once switched on.
- Also: `code_helper` reported files it never wrote; a rate-limited brain was
  compiled as Python; the latency test flaked under load; `eagle` /
  `eagle-dev` now separate merged from unmerged.

**Tests 1652 → 1781.**

### P0 — The mission loop  *(built 2026-08-11, live-verified, not yet driven by voice)*

The coordination layer. Every part already worked and the whole did not,
because nothing held the goal. `core/mission.py` + `mission_ladder` +
`mission_runners` + `mission_handoff` + `mission_store` + `actions/mission.py`,
reachable as the `mission` tool.

Verified live: three steps against real Wikipedia, unattended, 2.8s total.

**Not yet done, in order:**
1. **Drive it by voice.** Say "go to makerworld and download a laptop stand".
   The tool is wired and the prompt routes to it; nobody has spoken to it yet.
2. **The planner is rate-limited.** The free tier's 20 requests/day for
   gemini-2.5-flash was exhausted during the first smoke run. Planning is one
   extra call per mission — worth the quota, but it means a mission cannot
   start when the tier is spent. Reported honestly as "the brain is
   rate-limited", never as "the goal is impossible".
3. **Blueprint research** plugs into `_plan_locally` and nowhere else: look up
   the documented path before guessing at it, so the maze is a lookup rather
   than a search. The highest-value addition, deliberately after the loop.
4. **Delegated planning.** `context_pack()` is written and tested; nothing
   calls it yet. It is what turns "I am stuck" into "ask Claude/Antigravity
   for a different plan, with everything already ruled out attached".
5. **Native-app rungs.** The ladder is web + screen. The slicer is neither.

### P1 — Finish the tool contract rollout  *(advanced, not done)*

**11 of 20** tools now on the contract, up from 6. Unmarked failure returns
across `actions/`: **115 → 76**, AST-counted both sides.

`core/tool_result.py` gained two pieces that made the rest reachable:
`Failed`, a `str` subclass carrying guidance so a refusal can be marked where
it is DECIDED rather than at a boundary eleven frames up; and `settled()`,
because ending a migrated entrypoint with `normalize()` produced a silent
half-migration — failures carried `ok=False` and successes carried nothing at
all. That shipped once before being caught by running the tools.

Remaining, worst first: `swarm_orchestrator` (12), `file_processor` (11 — its
BOUNDARY migrated, its helpers never did), `browser_control` (10),
`game_updater` (9), `youtube_video` (5).

### P2 — Live voice baseline  *(no longer blocked — first numbers exist)*

Thirteen real turns measured. `to_voice` (speech_end → first_audio) has a
**median of 4.1s and a worst of 6.7s**. `spoken` (how long the reply talks
FOR) has a **median of 4.9s and a worst of 15.4s**.

Two separate problems, and the second was invisible before: even at zero
latency, a 4.9s reply means five seconds before the user can speak again. The
length instruction existed but sat at line 15 of a 19KB prompt; it is now
first and last, and `max_output_tokens` is wired as the dispatch-layer
backstop, left OFF until a value has been measured rather than guessed.

The `response` / `audio` split is not yet trustworthy — see the `first_token`
fix above. One more traced run settles whether the 4.1s is Google's or ours.

**Context measured:** ~13k tokens shipped per session — tool declarations
**60%**, prompt 37%, memory **1%**. Memory is not the problem. Whether that
context costs per-turn latency or only per-connection is answered by the next
traced run: compare turn 0's `response` against later turns.

### P3 — Video summarising  *(fixed, was never only quota)*

The roadmap said this worked and was blocked by quota. It was not: the CHAIN
was broken. `youtube_api` hands back titles and `summarize` refused anything
that was not a URL, while `_scrape_first_video_url` sat 250 lines up in the
same file doing exactly that job for `play`. The id is now carried from the
API payload, so no search is needed at all.

### P4 — Spotify, then GitHub  *(unchanged — blocked on the user)*

Neither started, and neither can be by an agent alone: each needs an OAuth app
created under the user's own identity, with the client id/secret placed in
`config/`. Fifteen minutes of his time unblocks both.

### P5 — `computer_control` / `computer_settings` timing  *(unchanged)*

`_type`'s 300ms and `_clear_field`'s 100ms remain, deliberately. Still no
observable condition to measure against.

Worth recording from this session's research: an accessibility-first actuator
would make part of this moot — measured on the user's own desktop, 45% of live
controls expose an AT-SPI action, and `do_action` runs in **0.2ms against
pyautogui's 103ms** without touching the cursor or needing window focus. That
is not primarily a speed argument: it is what lets the eagle work while the
user is using the machine. Web already works this way (headless, CDP clicks,
verified not to move the mouse); the desktop path throws the accessible handle
away and moves a physical mouse instead.

### P6 — Known-broken, reproduced, not yet fixed

- **WhatsApp: the first send after a web_agency session always fails** with
  `Target page, context or browser has been closed`, then relaunches and
  succeeds. ~12s per message, 6.5s of it re-doing navigate+login every time.
- **Core dump on exit**: `Failed to restore OpenGL context after clean-up.`
- **The eagle answers itself** — a spurious second turn with no user input.
  No mic gate during playback was found.
- **Page truncation is still positional.** `_spread` spends the 60-line budget
  across the page instead of taking the top, which helped Wikipedia (0 → 2
  subject nodes) and Python docs (4 → 10) but cost Hacker News (23 → 22). The
  real fix is ranking against the GOAL, not the layout.
- **Dependencies are unpinned** — all 30 of them, and the transcript-API
  rename already cost a silently dead feature once.

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
