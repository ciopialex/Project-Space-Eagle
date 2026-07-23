# Aethelark — Roadmap & History

*The journey from the original handoff to the present, plus what's next. Commit hashes are on branch `feat/tech-noir-ui` (and `main` for the Swarm‑OS phases). Last updated 2026‑07‑23.*

---

## 1. Where we are right now (2026‑07‑23)

- **Branch:** `feat/tech-noir-ui` (pushed to `origin`; `main` untouched).
- **Runs:** type **`eagle`** → native Dynamic Island pill → double‑click → web‑rendered command console (the exact approved artifact) driven by the real Gemini voice/swarm backend.
- **Verified working:** exact web UI (CASUAL + HARDCORE), real backend integration (boots, connects to Gemini, plays audio, runs tools), pill states (idle/listening/speaking/thinking/swarm), drag‑from‑anywhere (pill + dashboard), soft shadow, clean crossfade transition, artifact‑proportioned floating card, SPEAKING‑glitch/latency dedupe fix.

---

## 2. History

### Era 0 — Foundations (pre‑session, `…7f294f7`)
The classic PyQt/QPainter desktop assistant + the **Swarm OS** handoff (`for_fable_to_look_at.md`, 13 challenges). Built in phases:

| Phase | Commit | What |
|---|---|---|
| 1 | `0589cd7` | Persistent **PTY session pool** — single‑session agent memory (no duplicate terminals). |
| 2 | `8fa8ca4` | **pyte VT100 watcher** — thought‑stream extraction + deterministic auto‑approval of CLI prompts. |
| 3 | `886acf8` | **Swarm orchestrator** — git‑worktree isolation + live blackboard (`swarm_state.json`). |
| 4 | `bd8deb4` | Voice **interjection traps** (Ctrl‑C into PTYs) + self‑healing **sentinel** (stall recovery / fail‑over). |
| 5 | `94cba89` | **Reviewer agent** — offloaded verification (compile/test) + automated `--no-ff` merges. |
| 6 | `7d99dcc` | **Visual web verification** — headless Chromium loads changed web code, feeds screenshots back. |
| 7 | `83c72be` | **Dynamic Island facelift** — cached logo layers, vector paths, audio‑reactive breathing. |
| 8 | `7f294f7` | **Dashboard SSE** swarm telemetry + condensed **repo‑map** context packing. |

### Era 1 — Tech‑noir UI redesign (`513a3e9`)
The classic UI looked like "knockoff Jarvis." Redesigned to the **7EVEN / WEB7** tech‑noir language: the **Eagle Crest Core** replaced the cyan arc reactor; the adaptive **CASUAL / HARDCORE** surface; the **"Aethelark Remembers"** memory rail; functional starter chips; the swarm war room; **iOS‑grade spring motion**; and a critical fix — the **Doto + Manrope fonts are now registered** (they were silently falling back). Also `main.py` **session‑resumption resilience** (survive Gemini `1011`/GoAway without losing context). *Mockups: the approved artifact is preserved at `web/artifact_reference.html`.*

### Era 2 — The web‑rendered pivot (`4e795c3` → `bc47229`)
Realization: QPainter can't hit **pixel‑exact** CSS (no `backdrop-filter`, blend modes, etc.). Decision (see `Aethelark_Web_Pivot_Plan.md`): **render the real HTML/CSS in embedded QWebEngine**, keep the Python daemon, keep a native‑style pill. Delivered:

| Commit | What |
|---|---|
| `4e795c3` | Web pivot — exact artifact in QWebEngine + integrated backend (`aethelark_web.py`, `WebShellUI` adapter, message contract, `run_web.sh`). |
| `3aa6c6f` | Fixed SPEAKING pill glitch + voice latency (state **dedupe** — backend re‑asserts SPEAKING ~20×/s); **artifact‑proportioned** window (0.63×0.70, not fullscreen). |
| `de667b6` | Dropped the starfield in the app (the desktop is the background; card floats). |
| `bc47229` | Drag‑from‑anywhere (pill + dashboard), soft pill shadow, smooth crossfade transition, stop starfield RAF. |

---

## 3. Open items / next

### UI / UX
- **Exact shape‑morph animation** — the crossfade is smooth but not the artifact's literal *console‑shrinks‑into‑pill* morph. The robust path is a **single‑window refactor** (one transparent always‑on‑top window using the artifact's own collapse CSS, `setMask`/click‑through when collapsed). QWebEngine can't be per‑frame‑resized smoothly and `.grab()` is unreliable on GPU.
- **Pill transparency / shadow** — verified fine headless; depends on the desktop compositor.

### Voice
- **Turn‑detection tuning** — we're on Gemini defaults. The biggest *tunable* latency is the **end‑of‑turn silence wait**. Plan: **patient VAD by default** (don't cut off natural pauses) + an **explicit "done"/push‑to‑talk** trigger for snappy replies + **optimistic UI** (pill reacts on speech onset). Inference latency is model‑bound (irreducible).

### Backend / tools
- **Browser tab reuse** — "play that song again" opens a *new* Chrome tab; `browser_control`'s native‑open path always spawns a tab. Needs a reload/replay path that reuses the active tab + a prompt nudge.
- **Phase 3 — live swarm wiring:** pipe the real `swarm_orchestrator`/`sentinel`/`reviewer` state into `setSwarm(...)` (HARDCORE lanes/mission/timeline) + `window.pill.set('swarm', …)` (pill ambient readout). Treat conflicts as data hazards, blackboard as forwarding, reviewer as a pipeline stage.

### Governance
- **Constitution v0** — the inviolable articles (human's stop always wins; irreversible actions need a fresh yes), enforced as *hard* checks at the tool‑dispatch layer. See `Aethelark_Vision.md` §4.

### Distribution
- Optional **Tauri** host later (leaner than Chromium) — the web UI is portable, so the host is swappable without repainting a pixel. Never Electron.

---

## 4. Document index

- [`Aethelark_Vision.md`](Aethelark_Vision.md) — the **why** (thesis + governance model).
- [`Aethelark_Architecture.md`](Aethelark_Architecture.md) — architecture + data‑flow/UML diagrams.
- [`Aethelark_Specifications.md`](Aethelark_Specifications.md) — file map, message contract, config, deps.
- [`Aethelark_Web_Pivot_Plan.md`](Aethelark_Web_Pivot_Plan.md) — the web‑pivot decision + full message contract.
- [`CURRENT_STATUS.md`](CURRENT_STATUS.md) — one‑glance pickup point.
