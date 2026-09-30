# Working in this repo

Space-Eagle is the **body**: the host, the Dynamic Island pill, the voice loop,
the module bus and the confirmation gates. The **modules** are separate repos
and separate processes.

## Core Engineering & Testing Principles

- **Read the code, run the binary, inspect logs.** A test asserting state is a confident wrong answer waiting to happen. Manual testing and live session execution always beat mirror tests.
- **Two things earn a test:**
  1. **Irreversible or physically expensive failures:** A print starting on a physical machine burns filament and hours. A leaked memory file cannot be unpublished. An SEC rate-limit breach earns an IP ban.
  2. **Component seams:** Where two systems meet: tool <-> dispatcher, module <-> bus, host <-> QWebChannel bridge.
- **Do not write mirror tests:** Never write tests asserting that specific strings or identifiers appear inside implementation methods (`inspect.getsource`). If a method cannot be tested at a clean seam, refactor the method.
- **Contributing:** see `CONTRIBUTING.md` for how to run the suite and send a pull request.
- **Run the suite:** `.venv/bin/python -m pytest tests/ -q` (regression alarm, not a substitute for reading code).


## The brain in the harness is not the brain writing the code

**The eagle runs Gemini 2.5 Flash (voice).** Every tool description, error
message, guidance string and payload this repository produces is read by a
small, fast model in the middle of a spoken turn.

- **Say what to do next, not only what went wrong.** That is what
  `ToolResult.guidance` is for (`core/tool_result.py`).
- **Never leave a gap that needs inference to cross.** No jargon, no exception
  class names, no implying which of several tools to call next.
- **`ok` is the signal, prose is the fallback.**
- **Short beats complete.** A payload that overflows gets truncated mid
  structure, and the model acts on the half it received.
- **Nothing goes in `core/prompt.txt` that is not an instruction.** No dates, no
  provenance, no measurements, no invented examples about the user's life. A
  small model will read them back.

The rule of thumb: if closing the gap requires the reasoning of the model
*writing* the code, the gap is a bug. The model *running* the code cannot do it.

## Running it

The entry point is **`aethelark_web.py`**. `main.py` is a library exposing
`AethelarkLive`; the QPainter cockpit it used to launch was deleted, so
`python main.py` does nothing at all.

`eagle` is the launcher (`packaging/eagle`, rendered into `~/.local/bin/eagle`
by `install.sh`). It stops an older eagle still holding the previous code in
memory and prints the commit it is launching. `eagle install|remove|modules|
update` are the module door (`core/module_bus/installer.py`).

`install.sh` puts the app in `~/.aethelark/app` (a git checkout) and leaves the
rest of `~/.aethelark` to modules: `modules/<key>/` (each module's registered
manifest and island files), `venvs/<name>/` (each module's own environment),
`bin/` (their commands, searched before PATH). User data and keys live in the
platform data dir (`core/user_paths.py`), never in a checkout.

Behaviour defaults live in `core/prefs.py`: everything that speaks, pops up or
listens unprompted starts off, and Labs tools are not offered until switched on.

## Modules

Modules are separate repos and separate processes. The harness ships **no
module configuration**: a module's manifest and card files live in its own
package and `<module> register` copies them into `~/.aethelark/modules/<key>/`.
A manifest can carry `instructions` (added to the prompt only while installed)
and `examples` (dashboard suggestions), so routing for a module lives with it.

| module | repo |
| :--- | :--- |
| Aethelark-Trade (`atrade`) | https://github.com/ciopialex/Aethelark-Trade |
| Aethelark-3D (`a3d`) | https://github.com/ciopialex/Aethelark-3D |

The tests read module manifests from `tests/fixtures/module_bus/` (conftest
points `AETHELARK_MODULES_DIR` there). When a module's manifest changes, copy
it over the fixture: the fixture is a photograph and does not update itself.

## Core Architectural Reference

- **`docs/MODULE_CONTRACT.md`** is the interface a module is written against.

## Apple Human Interface Guidelines (HIG) System-Wide Mandate

**EVERY pixel and interactive component in this software must strictly obey Apple Human Interface Guidelines (HIG) and Apple Intelligence design standards.** This is not limited to Live Activities or the pill capsule — it governs the entire UI surface (Space-Eagle dashboard, settings, cards, modals, telemetry badges, controls, typography, and module HUDs).

### 1. Concentric Geometry (`ContainerRelativeShape`)
- **Mathematical Radius Invariant:** Every child element nested inside a parent with outer radius $R_{\text{outer}}$ and padding $P$ must strictly have:
  $$R_{\text{inner}} = \max(0, R_{\text{outer}} - P)$$
  Never guess or hardcode arbitrary inner radii. If $R_{\text{outer}} = 32\text{px}$ and $P = 12\text{px}$, inner radius must be strictly $20\text{px}$.
- **Continuous Curvature (Squircle):** All rounded rectangles must use continuous iOS superellipse corner curvature (`corner-curve: continuous` / smooth bezels) to eliminate harsh circular-to-straight transitions.

### 2. Materials, Vibrancy & Depth
- **Pitch-Black OLED Foundation:** Base dark surfaces use true OLED black (`#000000`, `rgba(10, 10, 14, 0.88)`).
- **Multi-Stage Optical Diffusion:** Translucent glass cards use `backdrop-filter: blur(28px) saturate(190%)` with a sub-pixel luminous specular highlight stroke (`1px solid rgba(255, 255, 255, 0.12)` or `inset 0 1px 0 rgba(255, 255, 255, 0.18)`).
- **Ambient Occlusion Shadows:** Elevated elements use dual-layer soft ambient shadows (`0 20px 48px rgba(0,0,0,0.55), 0 8px 18px rgba(0,0,0,0.35)`).

### 3. Apple Intelligence Fluid Aurora Edge Glow
- During active voice reasoning or model processing (`listening`, `thinking`, `speaking`), an organic multi-spectral fluid aurora border glow pulses along the perimeter without causing layout shift or content displacement.

### 4. Physical Spring Kinetics & Zero DOM-Tear Motion
- **Spring Physics:** All geometric morphs operate over a single 520ms spring (`--morph: 520ms; --spring: cubic-bezier(.34, 1.46, .5, 1)`). Overshoot settles naturally into target dimensions.
- **Zero DOM Tearing:** Never destroy, detach, or re-instantiate elements across view transitions. Compact leading/trailing elements must translate and scale in-place into their expanded card coordinates.

### 5. Numerical Telemetry Stability
- **Mandatory Tabular Numerals:** Apply `font-variant-numeric: tabular-nums` to every real-time counter, price, coordinate, temperature, layer number, and timer to guarantee character width parity and prevent jitter during updates.

### 6. Honest Physical Telemetry
- Never display synthetic mock data or fabricated zeroes (e.g. `0.0°C`). Display actual hardware readings or an honest pending state with a subtle physical shimmer.

---

## Dynamic Island Finite State Machine (FSM) from First Principles

Apple's Dynamic Island is an event-driven, priority-scheduled presentation container. Our implementation in [`web/pill.html`](web/pill.html) and [`aethelark_web.py`](aethelark_web.py) follows these first principles:

```
+-------------------------------------------------------------------------+
|                        Dynamic Island FSM Hierarchy                     |
+-------------------------------------------------------------------------+
| S0: IDLE REST         (168x46) -> Crest + 2.8s Breathing Mic Dot        |
| S1: VOICE CAPSULE     (320x62) -> Audio Reactive Waveform               |
| S2: COMPACT ACTIVITY  (320x62) -> CompactLeading + CompactTrailing      |
| S3: EXPANDED GLANCE   (440x236)-> Interactive 3D Turntable / Telemetry  |
| S4: EXPANDED DEEP     (440x364)-> 7-Layer Scorecard / Camera Diagnostic |
| S5: SYSTEM NOTICE     (320x52) -> Ephemeral Critical Alerts             |
+-------------------------------------------------------------------------+
```

1. **Orthogonal Voice Sub-FSM:** Voice states ($V \in \{\text{idle}, \text{listening}, \text{thinking}, \text{speaking}\}$) are strictly orthogonal to visual card presentation. When a card ($S_3, S_4$) is active, voice updates animate `#plive` tri-dots and the fluid aurora edge glow; they never tear down the card.
2. **Priority Preemption Table:**
   - Critical alerts ($\text{priority} \ge 80$) preempt active cards.
   - User-invoked cards suppress background ambient events ($\text{priority} < 80$).
   - Active browsing decks and user interactions suspend TTL decay up to `DWELL_CEIL_MS = 22500ms`.
3. **Deterministic State Transitions:** Transitions compute union hit regions, fire single Qt bridge resizes, synchronize 3D turntables, and report screen subject context back to the voice agent in real time.



