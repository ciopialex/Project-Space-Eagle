# Aethelark Space-Eagle: Project Overview & Target Behavioral Specifications

## 1. Present State of the Project (Ground Truth)

**Space-Eagle** is a desktop AI assistant and control HUD for Linux. It provides a real-time multimodal voice and vision interface with a custom floating desktop HUD ("Dynamic Island" pill) and an integrated system control suite.

### Current File Structure & Entry Points
```
Space-Eagle/
├── main.py                  # Primary application entry point & Gemini Live API WebSocket controller
├── ui.py                    # PyQt6 GUI interface (Dynamic Island PillWidget & Dashboard stack)
├── actions/                 # System control & AI tool execution suite
│   ├── agent_delegation.py  # Spawns external coding agent CLIs in gnome-terminal
│   ├── browser_control.py   # Multi-browser controller (Chromium/Chrome) with active session routing
│   ├── screen_processor.py  # Camera and screen capture module (OpenCV / PIL / mss)
│   ├── system_monitor.py    # Hardware telemetry (CPU/GPU/RAM metrics via psutil)
│   ├── open_app.py          # Native desktop application launcher
│   └── code_helper.py       # Code execution & file operations
├── core/                    # Low-level audio and LLM client handlers
│   ├── tts.py / stt.py      # PortAudio raw audio input/output stream management
│   ├── prompt.txt           # Master system prompt definition
│   └── llm_client.py        # Supplemental Gemini API helpers
├── memory/                  # Persistent configuration & state storage
│   └── config_manager.py    # Configuration loader (config/api_keys.json)
├── dashboard/               # Web control panel server (Quart/FastHTML server)
└── scratch/                 # Preserved developer helpers (inspect_layout.py, test_audio_resample.py)
```

### How Space-Eagle Operates Today
* **Voice Stream:** `main.py` opens a bidirectional PyAudio / PortAudio stream connected to Gemini Live API (`types.LiveConnectConfig`). Audio playback uses a 2000-frame queue with 100ms silence pre-buffering to prevent cold-start DAC pops.
* **Dynamic Island HUD:** `ui.py` renders a 240x84 desktop window. It features a solid obsidian base, specular top rim light, screen bleed halo, and a cached 4.5px PIL Gaussian blur drop shadow. Double-clicking toggles between PILL and DASHBOARD modes.
* **Agent Delegation:** When asked to delegate a coding task, `actions/agent_delegation.py` currently launches a `gnome-terminal` instance running `script -f -q -c "<agent_cmd>"` and tails the `/tmp` log file.

---

## 2. Target Behavioral Requirements (The Desired Experience)

This section defines **how Space-Eagle must behave** as it evolves. The implementation details are left flexible so advanced AI models can select the optimal technical architecture.

### Requirement A: Single-Session Agent Memory (No Duplicate Windows)
* **Current Behavior:** Calling an agent multiple times in the same directory opens a new `gnome-terminal` window each turn.
* **Target Behavior:** Eagle must maintain a continuous conversation with a single running agent instance per project directory. Follow-up instructions must route seamlessly into the existing active agent session without spawning duplicate windows or losing conversation history.

### Requirement B: Multi-Agent Team Synergy ("The Hive Mind")
* **Current Behavior:** Agents are executed individually in isolation.
* **Target Behavior:** Eagle must act as an Executive Swarm Conductor. When assigned a multi-faceted project, Eagle should be able to coordinate multiple AI tools (e.g. Antigravity CLI, Claude Code, Codex, Grok) working concurrently on the same codebase.
* **Mutual Awareness:** Sub-agents must be aware of each other's decisions in real time. If Agent A creates a backend API schema, Agent B (working on the frontend) should immediately receive that context and adapt its code to match.
* **Collision-Free Editing:** Concurrent agents must not overwrite each other's files or corrupt the codebase.

### Requirement C: Real-Time Thought & Intent Visibility
* **Current Behavior:** Eagle tails output text logs from `/tmp`.
* **Target Behavior:** Eagle must extract and display each agent's internal monologue (`<thinking>`), current task intent, and planned modifications in real time at 60 FPS without slow screenshot OCR.
* **Automated Approvals:** Eagle should automatically handle interactive CLI approval prompts (`1. Accept changes`, `[y/N]`) so agent workflows proceed without manual keyboard intervention.

### Requirement D: Delegated Code Review & Automated Merging
* **Target Behavior:** Eagle's live executive brain should not be bogged down reading multi-thousand line code diffs. Heavy code reviews, test suite validation, and git merges should be offloaded to a specialized sub-agent that reports a concise summary back to Eagle.

### Requirement E: Ultra-Low Latency & Spatial Visual Polish
* **Target Behavior:** Vision frame capture (webcam/screen) must feel instant (<10ms). The Dynamic Island HUD should feel weightless and liquid, with smooth resolution-independent vector scaling and audio-reactive animations.
