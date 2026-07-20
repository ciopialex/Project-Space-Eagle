# Aethelark Space-Eagle: Present Architecture & Upgrade Handoff

## 1. Present Codebase Implementation (Ground Truth)

### Subsystem 1: Voice & Vision Controller (`main.py`)
* **Gemini Live WebSocket:** Establishes a bidirectional streaming session using `google-genai` SDK (`types.LiveConnectConfig`, `response_modalities=["AUDIO"]`).
* **Audio Buffer Management:** 
  * Playback queue `queue.Queue(maxsize=2000)` prevents buffer overflow drops.
  * `PyAudio` / PortAudio `RawOutputStream` uses fixed block scheduling (`blocksize=2400`) and 100ms silence pre-buffering.
* **Tool Dispatcher:** Binds tool definitions from `actions/` and dispatches function calls returned by the model.

### Subsystem 2: Dynamic Island HUD (`ui.py`)
* **Widget Hierarchy:** Top-level `QMainWindow` hosting a `QStackedWidget` containing `PillWidget` (Index 1) and `_dashboard_container` (Index 0).
* **Pill Geometry & Padding:** Window size is `240x84`. `render_rect` is centered at `(24.0, 8.0)` with size `192x53.76` (3.5714 aspect ratio matching `AE_dynamic_island_cutout.svg`).
* **Visual Layer Stack in `paintEvent`:**
  1. *Cached Drop Shadow:* `_make_gaussian_shadow_image` renders a 4.5px PIL Gaussian blur shadow mask, cached on `shadow_key`.
  2. *Ambient Screen Bleed:* `screen_bleed` radial gradient (`bleed_ellipse` at `y=49.76` to `y=73.76`, leaving 10.24px safety margin).
  3. *Solid Base:* Piano black linear gradient `#141419` $\rightarrow$ `#08080A` $\rightarrow$ `#000000`.
  4. *Inner Vignette:* 6-pass clipped stroke for portal depth.
  5. *Logo Compositing:* Silver-titanium gradient with pulse color glow, anti-aliased via `_make_blurred_logo_image` (1.1px PIL Gaussian Blur).
  6. *Specular Highlights:* Top specular glass crescent and metallic border stroke.

### Subsystem 3: Agent Delegation Action (`actions/agent_delegation.py`)
* **Command Template Mapping:** Maps agent names (`antigravity_cli`, `claude_code`, `opencode`, `kimi`) to shell commands (e.g. `agy -i '{prompt}'`, `claude '{prompt}'`).
* **Subprocess Spawning:** Invokes `gnome-terminal` with `script -f -q -c "<agent_cmd>" /tmp/agent_delegation_<name>_<proj>.log; exec bash`.
* **Log Tailing:** Reads and cleans ANSI strings from the `/tmp` log file, forwarding lines to HUD logs.

### Subsystem 4: System Actions Suite (`actions/`)
* **`browser_control.py`:** Manages multi-browser launching and profile path resolution (`api_keys.json`).
* **`screen_processor.py`:** Handles webcam and desktop screenshot frame capture.
* **`system_monitor.py`:** Fetches CPU/RAM/GPU telemetry via `psutil`.
* **`open_app.py`:** Spawns native applications via `subprocess`.

---

## 2. Architectural Upgrade Paths for Target Behaviors

To achieve the desired software behaviors, an incoming engineer or model can implement the following architectural enhancements:

### Enhancement A: Persistent PTY Session Manager (Target: Single-Session Memory)
* **Architectural Change:** Replace stateless `subprocess.Popen("gnome-terminal")` in `actions/agent_delegation.py` with a **Session Pool**.
* **Mechanism:** Maintain a dictionary of active PTY file descriptors or terminal processes indexed by `(agent_name, project_dir)`. Re-use active PTY `stdin` handles for follow-up turns instead of launching new terminal windows.

### Enhancement B: Virtual VT100 Screen Buffer (Target: Thought Stream & Auto-Approval)
* **Architectural Change:** Integree an in-memory VT100 terminal screen emulator (such as Python `pyte`).
* **Mechanism:** Feed raw byte streams from agent PTYs into `pyte.Stream(screen)`. Extract 2D clean text snapshots to detect interactive CLI menus (`1. Accept changes`) and automatically inject keyboard responses (`1\n`). Parse `<thinking>` tags for HUD telemetry.

### Enhancement C: Git Worktree & Blackboard Sync (Target: Multi-Agent Hive Mind)
* **Architectural Change:** Create `actions/swarm_orchestrator.py`.
* **Mechanism:**
  * Partition concurrent agents into isolated Git Worktrees (`git worktree add`).
  * Implement `.space_eagle/swarm_state.json` as a real-time IPC blackboard for file locks and architectural decision broadcasting.
  * Delegate code reviews and merge conflict resolution to a specialized Reviewer Agent worker.

### Enhancement D: Hardware Frame Streaming (Target: Ultra-Low Latency Vision)
* **Architectural Change:** Upgrade `actions/screen_processor.py` to use GPU hardware encoding (VAAPI/NVENC/PyAV) to capture and stream visual frames in **<5ms**.
