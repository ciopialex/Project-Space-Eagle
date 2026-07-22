# Aethelark — The Web-Rendered Pivot: Plan & Execution

*Companion to `Aethelark_Vision.md`. That doc is the **why**; this is the **how** — the architecture decision and the phased execution plan for making the desktop UI look EXACTLY like the web artifact while staying blazing fast and efficient. Written down so the plan survives any context reset.*

Decision date: 2026‑07‑22. Snapshot branch before pivot: `feat/tech-noir-ui` (commit `513a3e9`).

---

## 1. The Decision (and why)

QPainter (native PyQt 2D) has a hard ceiling — it cannot reproduce `backdrop-filter: blur()`, CSS blend-mode grain, or exact CSS the way a browser does. To get **pixel-exact parity with the WEB7 / artifact design**, we render the real HTML/CSS/JS in a browser engine.

**Chosen architecture (forced mostly by physics):**

- **Aethelark Core** — a native local **Python daemon** with full OS access (the existing `main.py` + `actions/`: voice, memory, swarm/PTY, computer‑use, files). The vision's "synthetic operator in the seat" lives here, because a sandboxed web page *cannot* touch the PC.
- **Aethelark UI** — the **web artifact** (exact CSS, GPU‑composited: blur, starfield, grain, springs) rendered in **`QWebEngineView` embedded in the same PyQt process**, bridged to Python **in‑process via `QWebChannel`** (no IPC seam, no new language).
- **The pill** — stays **native QPainter** (already excellent; translucent always‑on‑top over the desktop is the one thing webviews do poorly).

**Rejected / deferred:**
- **Electron** — rejected. Heaviest footprint AND still leaves PyQt. No upside.
- **Tauri** (Rust host + OS webview, ~10MB) — *deferred* efficiency pass. The web UI + message contract are **portable**, so we can swap the host later without repainting a pixel — only if the ~130MB Chromium footprint ever hurts adoption. Measure first (timing closure).
- **Browser‑tab host** — can be offered later as a free "open in browser" mode (same web code), but NOT the primary, because the pill↔dashboard focus choreography needs one native process.
- **Rewriting backend in Rust for speed** — rejected. Aethelark is I/O‑bound; the critical path is the cloud LLM round‑trip, not Python. Optimizing Python is optimizing a path with timing slack.
- **Local LLM for "fast reflexes"** — rejected (real‑world: local models are slower AND dumber on consumer HW than cloud streaming). Reflex tier = non‑LLM only (VAD, wake‑word, barge‑in) + optimistic UI. All cognition stays cloud, fed by eager full‑duplex streaming.

---

## 2. Interaction Model (LOCKED)

- `eagle` → **single‑instance** daemon → opens **the pill only**, top‑center, always‑on‑top. (Running `eagle` again surfaces the existing pill; never spawns a second brain.)
- **Double‑click the pill** → **spring‑expands** into the web dashboard as a **maximized large window** (NOT kiosk‑fullscreen — that breaks focus handling; F11 = true fullscreen if wanted). Dashboard is **pre‑warmed** (created hidden at startup) so open is instant.
- **Collapse button** → spring back to the pill, dashboard hides. The deterministic control.
- **Focus‑loss behavior is MODE‑AWARE:**
  - **CASUAL dashboard = ephemeral overlay** → loses focus → springs back to the pill.
  - **HARDCORE dashboard = persistent war room** → stays open on blur (you're supervising a live swarm / "go get coffee"); closes only on explicit Collapse.
  - A **📌 pin** overrides in either mode.
- The pill's **ambient‑swarm readout** means you're never blind when collapsed.
- Pill ↔ dashboard transition uses the **spring morph** (`make_spring_curve`) — opening literally *is* the pill expanding into the console.

---

## 3. The Message Contract (v0 — the invariant everything bolts onto)

Transport is swappable (in‑process `QWebChannel` now → Unix‑socket + MessagePack / WebSocket later). The *schema* stays. Binary encoding + shared‑memory for high‑frequency streams (audio envelope, screen frames); JSON fine for low‑frequency control.

**Daemon → UI (push / subscribe):**
- `state` — LISTENING | SPEAKING | THINKING | WORKING | MUTED | STANDBY
- `log` — {speaker: sys|you|ae|swarm|net, text, ts}
- `memory` — [{icon, label, value}] (from long_term.json)
- `metrics` — {cpu, mem, gpu, net, tmp}
- `swarm.agents` — [{glyph, name, branch, status(work|review|block|idle), thought, adds, dels, file, elapsed}]
- `swarm.mission` — {repo, worktrees, merged, conflicts, progress, tasks, elapsed}
- `swarm.timeline` — [{ts, text, done}]
- `swarm.hazards` — [{type: conflict|stall, agents, detail}]  ← for hazard‑detection/forwarding
- `pill_status` — {mode(casual|hardcore), swarm{working, needs_you, total}}
- `connection` — {online, resuming}

**UI → Daemon (call / request):**
- `send_command(text)`
- `drop_file(path)`
- `set_mode(casual|hardcore)`  (also auto‑set by intent)
- `interrupt()`  ·  `halt_swarm()`  ·  `halt_agent(id)`
- `answer_agent(id, choice)`  ← resolve a "needs you" prompt
- `set_pin(bool)`  ·  `set_audio_level` subscription for the pill waveform

**Backpressure policy per stream:** drop‑oldest for real‑time (audio envelope, waveform); bounded‑block for critical (commands, merges). No unbounded queues (FIFO‑overflow discipline).

---

## 4. Roadmap (phases; commit per phase on a feat branch)

- **Phase 0 — Foundation.** Verify/install `PyQt6-WebEngine`. Build the two‑window shell: native pill (exists) + hidden pre‑warmed `QWebEngineView` dashboard in the same app. Wire the interaction model (single‑instance, double‑click open, collapse, mode‑aware blur, spring transition). Stand up the `QWebChannel` bridge with a stub page. **Deliverable:** eagle → pill → double‑click → blank web window springs open → collapse works.
- **Phase 1 — Port the artifact.** Drop the real artifact HTML/CSS/JS in as the dashboard (CASUAL/HARDCORE, memory rail, swarm lanes, all pill states in the web pill too, starfield + grain — now trivial). Static/mocked data first.
- **Phase 2 — Bridge the backend.** Push real state/log/memory/metrics from the Python daemon to the UI per the contract; wire UI actions back. Retire the QPainter dashboard (pill stays native).
- **Phase 3 — Live swarm.** Wire the real orchestrator → swarm.agents/mission/timeline/hazards → HARDCORE + pill ambient readout. Hazard‑detection/forwarding (conflicts = data hazards; blackboard = forwarding; reviewer = pipeline stage; sentinel = stall unit).
- **Phase 4 — Efficiency + polish.** Optimistic UI (pill reacts on VAD), eager streaming, backpressure everywhere, latency telemetry (timing closure). Optional: Tauri host migration; "open in browser" mode.

Parallelizable once the contract exists: UI work (Phase 1) and daemon bridging (Phase 2) can proceed concurrently against the frozen schema.

---

## 5. What carries forward (NOT wasted in the pivot)

- **Python backend** (`main.py`, `actions/`, swarm, memory, session‑resumption fix) — **unchanged**.
- **Native pill** (QPainter, all states, spring collapse) — **kept**.
- **The artifact IS the UI** — already built; ~80% of the front‑end work is done.
- **The QPainter dashboard** was the prototype that proved the design and **revealed the data contract** (§3). That was its job. Not waste — the spec came from it.

---

## 6. Guardrails (parallel track, from Aethelark_Vision.md)

Constitution v0 still pending. Sacred regardless of UI: **the human's stop always wins**; **irreversible actions require a fresh explicit yes**. The visual verifier is first‑class here. Enforce hard rules at the tool‑dispatch layer (belt = prompt, suspenders = dispatch gate).

---

*Ship correct first, then make it lean. Measure before optimizing the substrate. — carved 2026‑07‑22.*
