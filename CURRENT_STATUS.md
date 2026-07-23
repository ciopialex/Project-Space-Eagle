# Aethelark — Current Status (pickup point)

*One‑glance state of the project. Start here. Last updated 2026‑07‑23.*

## What it is
A voice‑and‑vision desktop **operator**: a native local daemon (Gemini Live brain) that types/clicks/browses/edits and **conducts a swarm of AI coding CLIs**, behind a Dynamic Island pill that expands into a web‑rendered command console (**CASUAL** / **HARDCORE**). Full thesis → [`Aethelark_Vision.md`](Aethelark_Vision.md).

## How to run
```bash
eagle                 # the web app (current) — pill → double-click → dashboard
# or
./run_web.sh          # same thing, in-repo launcher
python main.py        # the classic QPainter app (lighter fallback)
```
Requires `config/api_keys.json` with `gemini_api_key` (gitignored).

## State
- **Branch:** `feat/tech-noir-ui` (pushed; `main` untouched). Working tree clean.
- **Working:** exact web UI (both modes), real backend (connects to Gemini, audio, tools), pill states, drag‑anywhere (pill + dashboard), soft shadow, clean crossfade transition, artifact‑proportioned floating card, SPEAKING‑glitch/latency dedupe fix.

## Top open items (details in [`Aethelark_Roadmap.md`](Aethelark_Roadmap.md) §3)
1. **Exact shape‑morph animation** → single‑window refactor (crossfade works but isn't the literal morph).
2. **Voice** → patient VAD + push‑to‑talk "done" + optimistic UI (biggest tunable latency = end‑of‑turn wait; inference is model‑bound).
3. **Browser "play again"** → reuse/reload the active tab instead of opening a new one.
4. **Phase 3** → wire live swarm data into HARDCORE lanes + pill ambient readout.
5. **Constitution v0** → inviolable governance articles enforced at the tool‑dispatch layer.

## Documentation
| Doc | Contents |
|---|---|
| [`Aethelark_Vision.md`](Aethelark_Vision.md) | The **why** — thesis, universal‑interface principle, governance model |
| [`Aethelark_Architecture.md`](Aethelark_Architecture.md) | Architecture + **data‑flow / UML** diagrams (runtime, message contract, voice, swarm) |
| [`Aethelark_Specifications.md`](Aethelark_Specifications.md) | File map, message contract schema, config, dependencies, verification workflow |
| [`Aethelark_Web_Pivot_Plan.md`](Aethelark_Web_Pivot_Plan.md) | The web‑pivot decision + roadmap + message contract |
| [`Aethelark_Roadmap.md`](Aethelark_Roadmap.md) | Full history (Swarm‑OS phases → tech‑noir → web pivot) + what's next |

## To resume in a new conversation
Say: *"Continue the Aethelark web pivot — read `CURRENT_STATUS.md` and memory."* The assistant reloads from these docs + git + its memory files and picks up here.
