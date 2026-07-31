# Aethelark — Specifications

*Canonical component specs, file map, message contract, config, and dependencies. Companion to [`Aethelark_Architecture.md`](Aethelark_Architecture.md). Last updated 2026‑07‑23.*

---

## 1. Entry points

| Command | Runs | Result |
|---|---|---|
| `eagle` | `~/.local/bin/eagle` → `aethelark_web.py` | Web app: native pill → double‑click → QWebEngine dashboard, real backend |
| `./run_web.sh` | same as `eagle` | convenience launcher in‑repo |
| `python main.py` | classic app | Pure‑QPainter UI (lighter fallback) |

Launch flow (web): `WebShellUI("face.png")` builds the pill + dashboard windows on the **main thread**; a **runner thread** calls `wait_for_api_key()` → `AethelarkLive(ui)` → `asyncio.run(live.run())`.

---

## 2. File & module map

### Shell / UI
| File | Lines | Responsibility |
|---|---|---|
| `aethelark_web.py` | ~453 | Web app. `WebShellUI` (backend‑facing adapter, thread‑safe signals, pushes state to web + drives pill), `PillWebWindow` (transparent QWebEngine, `web/pill.html`), `DashWindow` (transparent QWebEngine, `web/dashboard.html`), `WebBridge`/`PillBridge` (QWebChannel `window.pybridge`). |
| `ui.py` | ~5094 | Classic QPainter UI (`MainWindow`, `PillWidget`, `HudCanvas`=Eagle Crest Core, swarm lanes, memory rail). Also shared helpers used by the web app: `make_spring_curve`, `load_app_fonts`, `_metrics`. |
| `web/build_app_ui.py` | — | Transforms `artifact_reference.html` → `web/dashboard.html`: full‑viewport override, hides presentation chrome + starfield, injects the bridge (`window.aethelark.*` setters + `window.pybridge` actions + drag). |
| `web/build_pill.py` | — | Generates `web/pill.html` from the artifact's exact pill CSS: transparent, all states, no clock, drag + double‑click. |
| `web/artifact_reference.html` | — | The approved design artifact — **source of truth** for the look. |
| `web/dashboard.html`, `web/pill.html` | — | Generated, deployed UI (embed fonts + eagle as data URIs). |

### Backend / brain
| File | Lines | Responsibility |
|---|---|---|
| `main.py` | ~1849 | `AethelarkLive`: Gemini Live session (`google-genai`), audio in/out, tool dispatch/scheduler (`ToolSpec`), session‑resumption resilience, `TOOL_DECLARATIONS`. `main()` wires UI + backend. |
| `core/llm_client.py` | ~586 | LLM helper client. |
| `core/prompt.txt` | — | System prompt / behavioral guidance (soft‑law layer — see governance). |

Speech has no module of its own. `core/tts.py` and `core/stt.py` were the
previous generation and were removed once the Gemini Live session took over
both directions; `core/installer.py` went with them, superseded by
`requirements.txt` driven from `install.sh` and `setup.py`. Nothing imported
any of the three.

### Tools (`actions/*`) — the operator's hands
`browser_control` (multi‑browser + automation), `file_controller`, `file_processor`, `open_app`, `computer_control`, `computer_settings`, `desktop`, `screen_processor` (webcam/screen capture), `web_search`, `send_message` (email/IG/Twitter), `youtube_video`, `weather_report`, `flight_finder`, `game_updater`, `reminder`, `proactive`, `code_helper`, `dev_agent`, `developer_mode`, `system_monitor`.

### Swarm (`actions/*`)
`pty_session` (persistent PTY pool keyed by agent+dir), `agent_screen` (pyte VT100 watcher → thought stream + auto‑approval), `agent_delegation`, `swarm_orchestrator` (git worktrees + blackboard), `swarm_sentinel` (stall/hazard), `swarm_reviewer` (compile/test/merge), `visual_verifier` (headless Chromium web checks), `repo_map` (AST context packing).

### Memory / remote
| File | Responsibility |
|---|---|
| `memory/memory_manager.py` | Long‑term user facts → `memory/long_term.json` (identity/preferences/projects/relationships/wishes/notes); `load_memory`, `remember`, `forget`, `format_memory_for_prompt`. |
| `memory/config_manager.py` | Feature config (morning brief, etc.). |
| `dashboard/server.py` (~839) | Local HTTPS control panel + **SSE swarm telemetry**; phone remote via QR (`dashboard/static/*`). |

---

## 3. Message contract

Transport today = **QWebChannel (in‑process)**. Schema is transport‑independent.

### Daemon → UI (push; `window.aethelark.*`, called via `runJavaScript`)
| Method | Payload |
|---|---|
| `setState(s)` | `"LISTENING"｜"SPEAKING"｜"THINKING"｜"WORKING"｜"MUTED"｜"STANDBY"` |
| `setMemory(list)` | `[{icon,label,value}, …]` |
| `setLog(lines)` | `[{speaker:"sys｜you｜ae｜net｜swarm", text}, …]` |
| `setMetrics(m)` | `{cpu,mem,gpu}` (strings like `"34%"`) |
| `setMode(m)` | `"casual"｜"hardcore"` |
| `setSwarm(d)` | `{mission:{repo,worktrees,merged,conflicts,progress,cpu,tasks,elapsed}, agents:[{glyph,name,branch,status,thought,adds,dels,file,elapsed}], timeline:[{ts,text,done}]}` |
| pill: `window.pill.set(state,data)` | `state ∈ idle｜listening｜speaking｜thinking｜swarm`; swarm `data={working,needs_you,total}` |

### UI → Daemon (call; `window.pybridge.*`, via QWebChannel)
`ready()`, `send_command(text)`, `set_mode(mode)`, `interrupt()`, `halt_swarm()`, `toggle_mute()`, `expand()`, `collapse()`, `minimize()`, `quit()`, `begin_drag(sx,sy)` / `drag_to(sx,sy)`.

### Backend → `WebShellUI` (the adapter interface AethelarkLive expects)
`set_state`, `write_log`, `set_audio_level`, `show_content`, `prompt_reconfig`, `notify_phone_connected`, camera stubs; properties `muted`, `current_file`, `assistant_name`, `on_text_command`/`on_remote_clicked`/`on_interrupt`, `root`, `_win._ready`; `wait_for_api_key`. All GUI‑affecting methods marshal to the main thread via signals.

---

## 4. Configuration & secrets

- `config/api_keys.json` — **gitignored**. Fields: `gemini_api_key` (required), `assistant_name`, `user_name`, `voice_name`, `ui_color`, `default_browser`, `os_system`, connected‑account creds, `camera_index`.
- `memory/long_term.json` — **gitignored** (personal facts).
- `config/certs/*` — self‑signed localhost cert for the dashboard HTTPS (tracked; low‑risk, regen‑able).

---

## 5. Dependencies (`requirements.txt` highlights)

`PyQt6`, **`PyQt6-WebEngine`** (Chromium — required by the web app), `google-genai`, `sounddevice`/PortAudio, `numpy`, `psutil`, `Pillow`, `pyte`, `fastapi`+`uvicorn`+`cryptography` (dashboard), `playwright` (visual verifier). Fonts bundled in `assets/fonts/` (Doto, Manrope); emblem `assets/images/eagle_white.png`.

---

## 6. Verification workflow (how UI changes are checked)

The GUI is validated **offscreen** without a physical display:
- Component render: `QT_QPA_PLATFORM=offscreen` + `widget.grab()` (QPainter) or a `QWebEngineView` load‑probe + `runJavaScript` DOM assertions.
- Real pixels: **`xvfb-run`** (virtual display) + screenshot; QWebEngine needs `QTWEBENGINE_DISABLE_SANDBOX=1` and `--no-sandbox`.
- Always `python -m py_compile` after edits; commit per slice on a feature branch.

*(Harness scripts live in the session scratchpad; the pattern is documented here so it can be recreated.)*
