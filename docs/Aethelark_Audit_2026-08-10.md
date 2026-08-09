# Aethelark — audit, 2026-08-10

Written overnight against a running repo. Everything below is either measured
or read off the code; where something is unverified it says so.

---

## 0. Read this first: two agents were editing this repo at once

A second Claude Code session (PID 102291, started 2026-08-04, cwd
`.claude/worktrees/web-agency`, branch `feat/voice-latency`) was **live and
writing into the main working tree** during this audit. It rewrote
`actions/grounding/web/grounder.py` and `tests/test_container_preference.py`
between a read and an edit four minutes apart, and later committed the work
to `main` as `834733c`.

Two consequences, and the second is the important one:

1. **The uncommitted work in the tree at midnight was not finished, and was
   not safe to commit.** It looked coherent — new functions, new tests, suite
   green. It had also caused a measured regression the other session was
   half-way through repairing: click reliability across the benchmark fell
   **66% → 33%** when `prefer_exact` began pulling hidden controls ahead of
   visible ones. The repair (`prefer_visible`, ranked above both other
   preferences) landed in `834733c`.

2. **A green test run proves nothing while another agent is writing.** The
   1652-passing baseline taken at 00:22 described a tree that no longer
   existed at 00:27.

This session's work was therefore done in an isolated worktree
(`.claude/worktrees/tool-contract`, branch `feat/tool-contract-rollout`) on
files the other session was not in. It **rebased onto `834733c` with zero
conflicts**, and the combined state passes **1699 tests**.

Before any future autonomous run here: `ps aux | grep [c]laude`, then
`ls -l /proc/<pid>/cwd`. Sample CPU twice to tell live from idle.

One side effect worth knowing: `test_structural_lookup_is_under_50ms_on_a_2000
_node_tree` asserts wall-clock time, and it **fails when two test suites run
concurrently**. It is not a regression — measured in isolation it sits at
8.6ms median against a 50ms budget, 6× headroom, identical on both branches.
But a red suite on a loaded machine now has an innocent explanation, and that
is exactly the kind of signal that gets "fixed" by raising a budget it did not
need.

---

## 1. State of play, measured

| | |
|---|---|
| tests, start of session | 1652 passing |
| tests, end of session | 1699 passing (+ the other session's web work) |
| branch | `feat/tool-contract-rollout`, 3 commits, rebased on `main` |
| tool contract coverage | 6 of 20 tools → **9 of 20** |
| unmarked failure returns | 128 → **82** |

---

## 2. The bug class this session went after

The roadmap's P1 was "finish the ToolResult rollout." That framing understates
it. The rollout is not cosmetic tidying — **every tool still returning bare
strings is a tool that can report a failure as a success**, and three of the
four bugs found tonight were exactly that, in tools nobody suspected.

### Why the existing pattern could not finish the job

`file_processor` migrated at the **boundary**: the entrypoint returns a
`ToolResult`, helpers keep their prose. That works while the entrypoint is
what *decides* the failure.

For most of the remaining queue it is not. `file_controller` decides "Access
denied" inside `_guard`, eleven frames down. `code_helper` decides "no
interpreter for .xyzzy" six frames down. By the time the entrypoint sees it,
only a string is left — and telling a failed string from a successful one by
reading its prose is the precise bug the contract exists to abolish.

### The mechanism added

`core.tool_result.Failed` — a `str` subclass carrying `guidance`.

Because it *is* a `str`, all 123 call sites, every format, and every existing
containment assertion (`assert "Access denied" in result`) behave identically;
migrating a tool is marking its refusals where they are decided, not a rewrite.
`normalize()` reads the verdict off the type. String operations on it (`+`,
`.strip()`) deliberately return plain `str`: a derived value was not the thing
the deciding code judged, so it degrades to today's behaviour rather than
claiming a failure it cannot vouch for.

---

## 3. Bugs found and fixed

All four were found by *migrating* the tool, not by looking for them.

**1. A rate-limited brain was compiled as Python.** `desktop_control` passed
`_ask_gemini_for_desktop_action`'s return straight to `_execute_generated_code`,
which `compile()`s it. That function returned `f"ERROR: {e}"` on failure — so a
429, routine on a free tier, was executed as source. The user asking to tidy
their desktop got *"Execution error: invalid syntax (<aethelark_desktop>, line
1)"*, as `ok=True`, for a request that would have worked a minute later.
`core/quota.py`'s relabelling could not catch it: that works on exceptions
reaching the dispatcher, and this one was stringified nine frames down.

**2. The same function built its Gemini client outside its own `try`** — and
the client reads `api_keys.json`. On a fresh install, the one error handler
written for this was unreachable for its most likely cause.

**3. A file that was never written was reported as saved.** `code_helper._write`
called `_save_file` and discarded the return value. An unwritable path still
produced *"Code written. Saved to: <path>"*, and the model, reading success,
told the user exactly where to find a file that does not exist. Verified
against the pre-fix state: it prints `✅ Written: .../afile/child.py` where
`afile` is a regular file.

**4. Eighteen refusals in `file_controller`** — "Protected directory, cannot
delete", "not overwriting", "Access denied" — reached the model with no status
at all.

---

## 4. What is left, ranked by what it costs the user

### P1 — finish the rollout (82 unmarked returns, 11 tools)

Measured per tool, by AST rather than by eye:

| tool | unmarked | note |
|---|---|---|
| `swarm_orchestrator` | 12 | |
| `file_processor` | 11 | boundary migrated; **helpers never were** |
| `browser_control` | 10 | high user contact |
| `game_updater` | 9 | |
| `youtube_video` | 6 | |
| `computer_settings` | 5 | |
| `send_message`, `open_app`, `messages_brief` | 4 each | |
| `web_search`, `flight_finder` | 3 each | |

`file_processor` is the interesting entry: it is counted as migrated, and its
*helpers* still return eleven unmarked failures. The boundary pattern reached
the entrypoint's own decisions and stopped. `Failed` now reaches the rest.

Each migration is ~40 minutes done properly (test first, prove the test fails
pre-fix, full suite). Expect roughly one real bug per two tools, on tonight's
rate.

### P2 — live voice baseline (**blocked on you, one minute of your time**)

Still true from the last roadmap: the trace is wired behind `AETHELARK_TRACE=1`
and **nobody has ever spoken to it with tracing on**. The reported 0.8s → 4s
regression has three fixed causes by inspection and none of them plausibly
accounts for 5×. One `[Trace]` line settles it.

Also free: say *"how are you doing"* and watch for a `🔧` line. If a tool fires
on a greeting, that is seconds of latency and a routing bug.

### P3 — "connected to everything" (**blocked on you, unavoidably**)

Spotify and GitHub are next in the roadmap and both are ready to build, but
neither can be started by an agent alone: each needs an OAuth app *you* create
under *your* identity, and the client ID/secret pasted into `config/`. Same for
any further Google scope. This is not a limitation of the code — it is what
account ownership means. Fifteen minutes of your time unblocks both.

### P4 — the two deliberate `computer_control` delays

`_type`'s 300ms and `_clear_field`'s 100ms. Unchanged and correctly so: they
have no observable condition, so they need a measurement harness before anyone
touches them. Changing them blind is guessing — and "a fixed delay standing in
for a measurement" is already this codebase's most common bug.

---

## 5. On "full autonomy overnight"

Worth stating plainly, because the gap matters for planning.

An agent session runs in **turns**, not as a daemon. This session did not run
for eight hours; it ran until the work in front of it was done and then
stopped. There is no mode where it keeps building unattended until morning,
and any claim otherwise would be a claim about hours of work that did not
happen.

What *is* real, and is what the roadmap's own design principle already says:
push judgement out of the model and into code. Every refusal marked tonight is
one the brain can no longer get wrong, on any model, at any hour. That
compounds; an overnight sprint does not.

The honest version of "delegate it and it just does it" is a tool layer that
cannot lie about what it did. That is what P1 finishes, and it is measurable:
82 remaining.
