# Strategic Briefing for Fable5: Space-Eagle Swarm OS Handoff

Welcome, Fable. You are taking the wheel of **Space-Eagle**—an open-source desktop AI command OS and multi-agent orchestrator. 

This document synthesizes our architectural discussions, strategic vision, desired software behaviors, and technical hints based on state-of-the-art open-source patterns across the AI ecosystem.

---

## 1. The Broader Vision & Objective

Space-Eagle sits at the top of the AI abstraction layer. Rather than operating as another standalone chat assistant or a locked-in single-provider agent, **Space-Eagle acts as the Head of the Swarm**.

It unifies rival tools and frontier models across AI labs—Google (Antigravity CLI / Gemini API), Anthropic (Claude Code), OpenAI (Codex), xAI (Grok), OpenCode, and AgentZero—into a single, synchronized autonomous software organization governed by real-time voice, vision, and a floating desktop HUD ("Dynamic Island").

```
                               ┌────────────────────────────────────────┐
                               │       SPACE-EAGLE COMMAND LAYER        │
                               │      Executive Voice & Vision HUD      │
                               └──────────────────┬─────────────────────┘
                                                  │
                    ┌─────────────────────────────┼─────────────────────────────┐
                    ▼                             ▼                             ▼
        ┌───────────────────────┐     ┌───────────────────────┐     ┌───────────────────────┐
        │   Antigravity CLI     │     │      Claude Code      │     │    OpenCode / Codex   │
        │ (Branch: feat/backend)│     │  (Branch: feat/ui)    │     │  (Branch: feat/tests) │
        └───────────┬───────────┘     └───────────┬───────────┘     └───────────┬───────────┘
                    │                             │                             │
                    └─────────────────────────────┼─────────────────────────────┘
                                                  ▼
                               ┌────────────────────────────────────────┐
                               │    REAL-TIME BLACKBOARD & WORKTREES    │
                               │  - Inter-Agent Decision Broadcasting   │
                               │  - Lock-Free Workspace Isolation       │
                               │  - Automated Review & Diffs Merge      │
                               └────────────────────────────────────────┘
```

---

## 2. Target Behaviors & Open-Source Hints

Below are the 13 key behavioral challenges to solve. Rather than dictating rigid code paths, we provide hints and references to how leading open-source repositories handle these problems.

### Challenge A: Single-Session Agent Memory (Eliminating Duplicate Windows)
* **Desired Behavior:** When the user delegates a task to an agent (e.g. `antigravity_cli` or `claude_code`) in a project directory, and later gives a follow-up instruction in the same directory, Eagle must continue the conversation inside the **same active agent instance**. It must not launch duplicate terminal windows.
* **Open-Source Hints & References:**
  * Look at how **Aider** and **Open-Interpreter** manage background terminal processes (`ptyprocess` / `pexpect`).
  * Look at how **SWE-agent** and **Devin** use persistent `tmux` session handles (`tmux send-keys` / `capture-pane`) or PTY file descriptors to stream follow-up prompts into running shells.

---

### Challenge B: Real-Time Thought Extraction & Deterministic Auto-Approvals
* **Desired Behavior:** Eagle needs to extract the sub-agent's internal reasoning (`<thinking>`), active intent, and current file edits at 60 FPS, while automatically answering interactive CLI prompt menus (`1. Accept changes`, `[y/N]`) so workflows run without manual keyboard intervention.
* **The Pitfall to Avoid:** Naive line-by-line regex on raw PTY text fails because modern rich CLIs (Ink/React, Rich, Bubbletea) use ANSI escape sequences (`\x1b[1A`, `\x1b[2K`) to animate spinners and redraw lines in-place, fragmenting text lines.
* **Open-Source Hints & References:**
  * **Virtual Terminal Emulation:** Reference how **`pyte`** (Python Terminal Emulator) or **`xterm.js`** operate. By feeding raw PTY streams into an in-memory 2D VT100 screen buffer, you get an exact clean snapshot of what a human sees on screen, making menu detection and auto-approval (`1\n`) 100% deterministic.
  * **Headless Event Streams:** Modern tools like **Claude Code** and **OpenCode** expose non-interactive JSON-RPC / IPC streaming flags (`--log-format json` or `--dangerously-skip-permissions` / `-p`), allowing direct event subscription without screen scraping.

---

### Challenge C: Multi-Agent Team Synergy ("Cigarette Break" Synapses)
* **Desired Behavior:** Sub-agents must not work in blind silos. When one agent (e.g. Antigravity CLI on the backend) makes a structural decision (such as defining a JSON API schema), Eagle should immediately broadcast that context into parallel agents (e.g. Claude Code on the frontend) so they adapt in real time.
* **Workspace Collision Avoidance:** Concurrent agents working on the same repository must never overwrite each other's files or corrupt git state.
* **Open-Source Hints & References:**
  * **Git Worktrees:** Reference how **AgentZero**, **Aider**, and **SWE-bench** run parallel agents. `git worktrees` (`git worktree add`) are the 2026 industry-standard isolation primitive, allowing multiple agents to code concurrently on separate branches in the same repository history without stashing or file collisions.
  * **Blackboard State Sync:** Reference how **CrewAI**, **AutoGen**, and **LangGraph** manage shared blackboard ledgers (`swarm_state.json`) for file locking, architectural event broadcasting, and task state tracking.

---

### Challenge D: Real-Time Human Voice/Vision Interjections
* **Desired Behavior:** When the user watches the HUD log or terminal output and speaks: *"Hey Eagle, stop Claude, tell it to use Rust Axum instead of Actix"*, Eagle must immediately intercept the target sub-agent's PTY session, send a graceful SIGINT (`Ctrl+C`), and inject the user's verbal interjection.
* **Open-Source Hints & References:**
  * Reference **Open-Interpreter**'s interrupt handlers and **Aider**'s keyboard interrupt traps. PTY streams can receive `\x03` (`Ctrl+C`) bytes directly to halt sub-agent loops mid-generation before piping new user instructions.

---

### Challenge E: Self-Healing Agent Recovery & Circuit Breakers
* **Desired Behavior:** If a sub-agent gets stuck in an infinite error loop, hits an unresolvable API rate limit, or hallucinates syntax errors, Eagle should intervene autonomously. It should capture the failing context, terminate the stuck process, and re-delegate the unblocked task to an alternative sub-agent (e.g. handing a stuck `antigravity_cli` task over to `claude_code` or `opencode`).
* **Open-Source Hints & References:**
  * Reference **AgentZero**'s fallback delegation loops and **CrewAI**'s agent retry strategies.

---

### Challenge F: Repository Context Packing & Tree-Sitter Maps
* **Desired Behavior:** Sub-agents must receive condensed, high-signal codebase context without overflowing token limits or reading irrelevant binary files.
* **Open-Source Hints & References:**
  * Reference **Aider's `repo-map`** architecture using `tree-sitter`. Generating a compact AST map of class declarations, function signatures, and exported interfaces across the repository gives agents 90% of architectural context using only 10% of token window size.

---

### Challenge G: Plug-and-Play `AgentAdapter` Extensibility
* **Desired Behavior:** As new AI tools emerge from frontier labs (e.g. Grok Build CLI, Gemini 3.0 CLI, Codex 2.0), adding support to Space-Eagle should require only a simple 10-line adapter configuration.
* **Hint:** Keep the `AgentAdapter` base class modular (`command_template`, `pty_args`, `thought_parser`, `prompt_auto_responder`).

---

### Challenge H: Offloaded Code Review & Automated Verification
* **Desired Behavior:** Eagle's executive brain (the voice/vision stream) should remain fast and responsive rather than consuming token bandwidth reading multi-thousand line diffs.
* **Open-Source Hints & References:**
  * Look at the **Manager-Reviewer Pattern** in **CrewAI** and **MetaGPT**. When sub-agents report task completion, Eagle triggers a specialized **Reviewer Agent** worker to run git diffs, execute test suites, handle merge conflict resolution, and return a concise status summary to Eagle.

---

### Challenge I: Spatial Visual Polish & Zero-Latency Hardware Pipeline
* **Desired Behavior:** Vision capture (screen and webcam) must feel unperceivably fast (<10ms). The Dynamic Island pill HUD in `ui.py` should feel liquid, spatial, and weightless.
* **Open-Source Hints & References:**
  * Reference hardware video capture frameworks (PyAV / VAAPI / NVENC) for zero-copy frame encoding.
  * For `ui.py`, consider migrating CPU `QPainter` drawing paths to GPU fragment shaders (`QOpenGLWidget`) and converting embedded raster SVG elements into true cubic Bezier vector paths.

---

### Challenge J: Autonomous Visual Web Verification Coupling
* **Desired Behavior:** When a sub-agent modifies web code (HTML/CSS/JS), Eagle should automatically trigger [actions/browser_control.py](file:///home/shennyonthebeat/Projects/Space-Eagle/actions/browser_control.py) (Playwright / Chromium) to load the page, capture a screenshot, and feed the visual output back to both the agent and HUD for visual verification.
* **Open-Source Hints & References:**
  * Reference **Playwright** automated visual assertions and **AgentZero** computer-use visual feedback loops.

---

### Challenge K: Host Security & Sandboxed Process Execution
* **Desired Behavior:** Destructive or high-risk sub-agent commands (e.g. `rm -rf`, system package installs, network calls) must be executed within isolated sandbox boundaries, with Eagle requesting voice/HUD confirmation before risky operations.
* **Open-Source Hints & References:**
  * Reference **Bubblewrap (`bwrap`)**, **Docker containers**, and **Landlock Linux LSM** sandboxing used in **Devin** and **AgentZero**.

---

### Challenge L: Acoustic State Micro-Interactions & Audio Cues
* **Desired Behavior:** Eagle should provide subtle ambient audio cues (soft chimes / state tones) when an agent completes a sub-task, hits a block, or requires user input—giving the user ambient awareness without needing to look at the screen.
* **Open-Source Hints & References:**
  * Reference **Apple VisionOS** tactile audio cues and **macOS system sound design**.

---

### Challenge M: Dashboard Web Real-Time Telemetry Streaming
* **Desired Behavior:** The local web control panel in [dashboard/server.py](file:///home/shennyonthebeat/Projects/Space-Eagle/dashboard/server.py) should stream live sub-agent thought trees, terminal logs, and active Git Worktree diffs to web clients over Server-Sent Events (SSE) or WebSockets.

---

## 3. Target Codebase Components & File Map

To assist Fable5 in correlating behaviors to the codebase, here is the target mapping of files to modify or create:

* **[actions/agent_delegation.py](file:///home/shennyonthebeat/Projects/Space-Eagle/actions/agent_delegation.py):** Upgrade to persistent PTY session registry, `pyte` virtual terminal screen parsing, and auto-approval input injection.
* **[NEW] `actions/swarm_orchestrator.py`:** Create Git Worktree partition manager (`git worktree add`), real-time Blackboard telemetry sync (`.space_eagle/swarm_state.json`), and file-locking engine.
* **[actions/screen_processor.py](file:///home/shennyonthebeat/Projects/Space-Eagle/actions/screen_processor.py):** Upgrade to hardware-accelerated video frame capture (VAAPI / NVENC PyAV integration).
* **[ui.py](file:///home/shennyonthebeat/Projects/Space-Eagle/ui.py):** Upgrade `PillWidget` rendering with GPU shader overlays, audio-reactive FFT breathing, and vectorized path scaling.
* **[dashboard/server.py](file:///home/shennyonthebeat/Projects/Space-Eagle/dashboard/server.py):** Add SSE / WebSocket real-time swarm telemetry streaming.

---

## 4. Recommended Handoff Order

1. **Phase 1:** Refactor `actions/agent_delegation.py` to support persistent PTY session handles per `(agent_name, project_dir)`.
2. **Phase 2:** Integrate `pyte` virtual terminal screen buffering to extract thought streams and auto-approve interactive CLI prompts.
3. **Phase 3:** Build `actions/swarm_orchestrator.py` with `git worktree` isolation and `.space_eagle/swarm_state.json` real-time blackboard sync.
4. **Phase 4:** Add real-time human interjection traps (`Ctrl+C` PTY interrupts) and self-healing agent circuit breakers.
5. **Phase 5:** Connect the delegated Reviewer Agent for automated worktree branch verification and git merges.
6. **Phase 6:** Integrate browser visual verification coupling with [actions/browser_control.py](file:///home/shennyonthebeat/Projects/Space-Eagle/actions/browser_control.py).
7. **Phase 7:** Elevate `ui.py` with GPU shader layers and resolution-independent vector rendering.

Good luck, Fable. You have the full context, strategic vision, ecosystem references, and target file mapping. Take the wheel!
