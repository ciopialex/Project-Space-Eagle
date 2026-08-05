# Web Agency — Design

**Created:** 2026-08-04
**Status:** v1 implemented 2026-08-05 — see `docs/superpowers/plans/2026-08-04-web-agency.md`

**Measured structural coverage at v1:** 71.8% average across 5 sites, measured
2026-08-05 (`tools/web_coverage.py`): en.wikipedia.org 60.7% (hit the
`MAX_NODES=600` ceiling — the true number is lower than this floor),
news.ycombinator.com 86.6%, developer.mozilla.org/.../CSS 21.0% (1,150 of
1,151 misses traced to one cause: content inside a closed `<details>` reads
as unnamed because Chromium's `innerText` is empty there even though
`textContent` is not), www.python.org 99.6%, ro.wikipedia.org 91.2%.

**Re-measured 2026-08-05 after task 12** (`tools/web_coverage.py`, same 5
sites, same method), which fixed the closed-`<details>` gap above
(`accName()` now falls back to `textContent` when `innerText` is empty,
gated so a control stays collected-but-not-`SHOWING` rather than falsely
claiming to be clickable) and made the `MAX_NODES=600` ceiling honest (a
`truncated` flag the collector now reports instead of silently under-
counting, and a viewport-preference cut so a page over the cap keeps
controls near the user's current view instead of whatever happens to come
first in document order): **76.7% average.** en.wikipedia.org 60.7%
(unchanged — still hits the ceiling, still a floor; its near-viewport
content already fell within the first 600 elements in document order, so
viewport-preference changed nothing measurable on this particular page),
news.ycombinator.com 86.6% (unchanged), developer.mozilla.org/.../CSS 40.8%
(up from 21.0% — the closed-`<details>` gap is fixed, but that now
surfaces a genuine ceiling instead of a naming bug: the page has 1,694
named candidates and only 600 can be shown, so this is *also* now a floor,
correctly flagged, not the true number), www.python.org 99.6% (unchanged),
ro.wikipedia.org 95.9% (up from 91.2%). Two of five sites' numbers are
floors, not scores, and the collector now says so in its own output rather
than the coverage script having to infer it.
Automatisms and the submission gate remain unbuilt.

## Why

Asked what it can do on YouTube, the eagle answers: play a song, run a search.
It cannot like, comment, post, or open history. Not because those are hard —
because nobody wrote those five functions.

`actions/youtube_video.py` is `_scrape_first_video_url`, `_extract_video_id`,
`_get_transcript`, `_scrape_trending`. A bespoke scraper, not a way of *using*
YouTube. `actions/browser_control.py` is 1,274 lines of which almost none
concern web pages — profile directories, snap detection, executable discovery,
default-browser resolution. Its payload is `_open_native(url, browser)`. Across
those 1,274 lines there are **five** references to DOM, ARIA or accessibility.

So today the eagle can open a browser and has hand-written scrapers for a
handful of sites. It cannot perceive or act inside a page.

Adding functions does not fix this. It is a treadmill that scales linearly with
sites, against a web of billions. A human thrown at an unfamiliar site works it
out — that is the capability to build, and it is the only one that compounds.

## The two problems

Conflating these is why most web agents fail.

| | Problem | Solved by |
|---|---|---|
| Perception | What controls exist, what are they called, what state are they in | `WebGrounder` — mechanical, testable |
| Planning | Given "do my taxes", which control advances the goal | The model — but only over what it is shown |

Perception is yesterday's grounding work pointed at a different backend.
Planning hinges entirely on the page representation, below.

## Architecture

Four pieces. Three reuse what already exists.

### 1. `WebGrounder`

Implements the existing `Grounder` protocol, backed by Playwright/CDP. Returns
the same `Element` (name, role, bounds, states, value), so `wait_for`,
`act_and_verify` and the actionability checks work **unchanged** — the same
reason Windows and macOS slotted in without touching them. ARIA roles normalise
through `actions/grounding/roles.py` exactly as UIA and AX do.

The web is a *better* surface than the desktop for this: ARIA gives
`role=button, name="Add to Cart"` directly, CDP exposes event listeners, and
Playwright's own actionability model is the one already transplanted into
`actions/grounding/actionability.py`.

### 2. `PageSense` — tiered representation

Cheapest sense first; escalate only when it comes back thin.

1. **Accessibility snapshot** (default) — compact tree of interactive elements
   with role, name, state and a stable `ref`. Roughly 50-200 lines for a complex
   page. The model addresses elements by `ref`, so there is no coordinate
   guessing — the failure mode that produced a hallucinated `(1420, 337)` on a
   YouTube page during testing.
2. **Screenshot** — on escalation only.

Escalation triggers, as a policy object so they are tunable without touching
the grounder: snapshot below a node threshold; the model explicitly asks; an
action fails twice.

`page.screenshot()` renders from the browser compositor, not the display, so it
works on a background tab, an unfocused window, or a headless browser. The
escalation tier therefore survives the eagle running invisibly — which is what
makes concurrency possible.

### 3. `EagleBrowser` — its own persistent context

One Playwright context under `user_paths.user_data_dir()/browser`. Launched
once, kept warm, headless-capable.

Rejected alternatives, and why:

- *Attach to the user's running Chrome*: inherits every login instantly, but
  shares one window set with the user — steals focus, changes their tabs, and
  cannot run in the background. Kills "play a song while doing my taxes".
- *Seed cookies from the real profile*: cookie stores are keyring-encrypted per
  OS, break on Chrome updates, and read credentials the user did not hand over
  deliberately.

The cost of a persistent profile is one login per site. That is a feature: an
explicit, visible moment where the user grants the eagle access to a given
site, rather than silent inheritance of every open session.

Sites behind 2FA include a supervised handoff — the eagle pauses and the human
completes the challenge. The same handoff covers human-verification checks;
that is what a human assistant does ("I need you for this bit"), and it is the
only approach that does not degrade as those systems change.

### 4. Actuation split

Web actions actuate through CDP (`page.click(ref)`); native actions keep
synthetic input. A deliberate rule: **best available channel per surface**.

This departs from "the Eagle IS the API" as argued for native apps, and the
departure is intentional. A background window has no cursor to move, and
programmatic actuation is what allows web work to run alongside the user
instead of fighting them for the mouse.

## Automatisms (v2, designed for now)

Humans build muscle memory for repetitive tasks and stop re-perceiving them.
The same idea applies here, with one correction: an automatism records
**semantic steps**, never coordinates.

    click "Sign in" -> type into "Email" -> click "Next"

A coordinate sequence breaks on a window move, a resolution change or a theme
switch. A semantic sequence survives all of those and breaks only on a genuine
redesign.

Critically, an automatism is a **fast path, never a replacement**. It replays,
`act_and_verify` checks the outcome, and on failure the step falls back to
perception and is re-learned. Muscle memory that fails silently is worse than
no muscle memory.

This is the same tiering as everywhere else — remembered, perceived, guessed —
and `actions/grounding/cache.py` is already its single-step form.

## Out of scope for v1

- Multi-tab orchestration
- Form-filling heuristics
- **Anything that submits.** The motivating example (file taxes on ANAF SPV)
  ends in an irreversible act. Submission belongs behind the Constitution's
  fresh-explicit-yes gate, and that gate must exist before submission is wired,
  not after.

## Testing

Same shape as the grounding work: an injectable page seam so matching and
tiering are tested without a browser, plus a small number of live smoke tests
against real pages. The escalation policy gets its own tests — that is where
the subtle bugs will be.

Success is measured as *"share of controls found structurally on a site never
seen before"*, not *"number of sites supported"*. The second metric is the
treadmill this design exists to leave.

## Risks

- **Div-soup and canvas apps** defeat semantic perception. Vision remains the
  fallback; the tiering already handles it, but coverage will not be uniform.
- **Auth walls** (certificate, 2FA) are not a perception problem and no amount
  of better sensing solves them. Scope them early on any target site.
- **Deleting the scrapers too eagerly.** `youtube_video.py` keeps working until
  the general path demonstrably covers each case. No big-bang cutover.
