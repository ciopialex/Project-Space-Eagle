# Aethelark — Current Status (pickup point)

*One-glance state of the project. Start here. Last updated 2026-08-13.*

## What it is
A voice-and-vision desktop **operator**: a native local daemon (Gemini Live
brain) that types/clicks/browses/edits and **conducts a swarm of AI coding
CLIs**, behind a Dynamic Island pill that expands into a web-rendered command
console. Full thesis → [`Aethelark_Vision.md`](Aethelark_Vision.md).

## How to run
```bash
eagle                 # the web app — pill → double-click → dashboard
# or
./run_web.sh          # same thing, in-repo launcher
python main.py        # the classic QPainter app (lighter fallback)
```
Requires `config/api_keys.json` with `gemini_api_key` (gitignored).

## State
- **Branch:** `main`. Working tree has today's mission-loop work uncommitted
  (see below) — not yet reviewed into a commit.
- **The active subsystem right now is not the UI shell — it's the mission
  loop**: give the eagle a goal in words, it plans steps, walks each one
  through an escalating ladder of strategies (DOM → accessibility tree →
  pixels), and only advances when the world is re-observed to have actually
  moved. Entry point: `actions/mission.py` (`start` / `next` / `status` /
  `abandon`).

## What's proven today, not just built
- **Download → read a local file → fill a template → upload → submit**,
  end to end, against a site the team owns (`tools/mission_e2e.py` +
  `tools/testsite/`). Verified from the *server's* record of what it
  received, never from the eagle's own report. Clean across 4+ consecutive
  runs.
- **A real production bug, found by live reproduction and fixed**:
  `browser_control` (the user-facing, visible browser) used to fetch/launch
  a real Chrome window *before* checking whether the requested action was
  even valid — so a bad action name popped a visible window open, then
  refused. Root-caused with a live window monitor (not guessed), fixed by
  moving the validation before the browser touch, pinned with two
  regression tests.
- **One-time mission authorization.** A step that would commit something
  irreversible (submit, buy, pay) no longer gets asked about mid-mission —
  the plan is scanned up front, the human is asked once before anything
  runs, and the answer (`Mission.authorized`) travels with every later step.
  Never exposed to the model's own tool schema — only mission code can set
  it.
- A full architecture reference for this subsystem — the step loop, the two
  browsers, the ladder rules, the consent gate — exists as a diagram; see
  `Aethelark_Architecture.md` §8 for the same content in git-tracked form.

## Not yet proven
- **A genuinely unknown, external site.** `tools/mission_smoke.py` exists
  (default goal: MakerWorld), the real model plans it live — but "success"
  there is only the mission tool's own report, with no independent
  server-side check the way the owned rig has. This is the actual next test.
- **Voice-driving the mission loop.** The tool is wired and the prompt
  routes to it; nobody has spoken a mission goal to it yet.

## Top open items (details in [`docs/Aethelark_Roadmap.md`](docs/Aethelark_Roadmap.md))
1. **MakerWorld / unknown-site test** — prove the loop generalizes past the
   owned rig, with a real independent check, not a self-report.
2. **Pre-mission research** — a cheap web search for a site's known shape,
   folded into the same planning call as a soft prior ("commonly reported,
   may be stale"), never trusted over the live DOM. Scoped to bare-goal
   planning only. Proposed, not built.
3. **Drive the mission loop by voice.**
4. **Ten clean missions in a row** on unrelated sites is the bar for calling
   the loop reliable enough to ship.
5. **Finish the `ToolResult` contract rollout** — 11 of 20 tools migrated.

## Documentation
| Doc | Contents |
|---|---|
| [`Aethelark_Vision.md`](Aethelark_Vision.md) | The **why** — thesis, universal-interface principle, governance model |
| [`Aethelark_Architecture.md`](Aethelark_Architecture.md) | Architecture + data-flow diagrams (runtime, message contract, voice, swarm, **mission loop**) |
| [`Aethelark_Specifications.md`](Aethelark_Specifications.md) | File map, message contract schema, config, dependencies, verification workflow |
| [`docs/Aethelark_Roadmap.md`](docs/Aethelark_Roadmap.md) | The living state-of-play doc — what works, measured, what's next, in priority order |
| [`Aethelark_Google_Setup.md`](Aethelark_Google_Setup.md) | One-time Google OAuth connection walkthrough |

## To resume in a new conversation
Say: *"Continue the mission loop work — read `CURRENT_STATUS.md` and
`docs/Aethelark_Roadmap.md`."* The assistant reloads from these docs + git +
its memory files and picks up here.
