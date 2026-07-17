# Aethelark (Space-Eagle) Technical Specification & Optimization Guide

This specifications document provides a technical walkthrough of Aethelark’s current architecture, pipelines, and performance bottlenecks. It is designed to get an optimizing agent up to speed instantly.

---

## 1. Technology Stack & Core Engine
*   **User Interface (Front-end)**: PyQt6 running a custom glassmorphism Dashboard and a floating Dynamic Island widget (`PillWidget` inside `ui.py`).
*   **Runtime Loop (Back-end)**: Asynchronous event loop (`asyncio` thread inside `main.py`) coordinating audio capture, receive loops, and tool execution.
*   **LLM Connection Engine**: Gemini 2.5 Multimodal Live API WebSocket session (`models/gemini-2.5-flash-native-audio-preview-12-2025` using the `google-genai` SDK).
*   **Audio Capture & Playback**: `sounddevice` using PyAudio/PortAudio drivers.
    *   *Upstream (Input)*: 16kHz mono, int16 PCM (Chunk size: 1024 samples / 64ms buffer).
    *   *Downstream (Output)*: 24kHz mono, int16 PCM.

---

## 2. Source Code Layout
*   **[main.py](file:///home/shennyonthebeat/Projects/Space-Eagle/main.py)**: The central brain. Implements the connection loop, audio callbacks, and tool dispatcher.
*   **[ui.py](file:///home/shennyonthebeat/Projects/Space-Eagle/ui.py)**: PyQt6 window logic. Contains `PillWidget` (states: `STANDBY`, `LISTENING`, `THINKING`, `WORKING`, `SPEAKING` matching specific colors and breathing pulse rates) and size layout configurations.
*   **[core/stt.py](file:///home/shennyonthebeat/Projects/Space-Eagle/core/stt.py)** & **[core/tts.py](file:///home/shennyonthebeat/Projects/Space-Eagle/core/tts.py)**: Offline text-to-speech/speech-to-text engines (used for briefings or fallback modes).
*   **[actions/](file:///home/shennyonthebeat/Projects/Space-Eagle/actions/)**: The tool suite called by Gemini. Includes `open_app.py`, `system_monitor.py`, `web_search.py`, etc.

---

## 3. Data Pipelines (Current Flow)

### A. Upstream Audio Capture
1.  `sounddevice.InputStream` registers a thread-safe callback.
2.  The callback converts incoming buffer frames to raw bytes and places them in `out_queue`.
3.  The async task `_send_realtime` pops from the queue and sends a binary media input package to Gemini over the WebSocket.

### B. Downstream Audio Playback
1.  The async task `_receive_audio` reads WebSocket responses.
2.  Incoming audio chunks from `response.data` are sliced into 50ms chunks (2400 bytes) to ensure low-latency interruption capability and pushed to `audio_in_queue`.
3.  The async task `_play_audio` consumes this queue, making blocking writes (`stream.write`) to `sounddevice.RawOutputStream` via `asyncio.to_thread`.

### C. Tool calling and Execution
1.  Gemini sends a `tool_call` event containing list of `function_calls`.
2.  `_receive_audio` catches the event and routes it to `ToolCallHandler`.
3.  `ToolCallHandler` iterates through tool calls **sequentially** (blocking loop):
    ```python
    for fc in response.tool_call.function_calls:
        fr = await self._execute_tool(fc)
    ```
4.  `_execute_tool` runs synchronous action handlers on background threads in a thread pool (`loop.run_in_executor`).
5.  Results are accumulated and returned to Gemini Live.

---

## 4. Key Performance Bottlenecks & Targets

### I. Sequential Tool Calls (Parallelism Fix)
*   **Target**: In `main.py` (`_receive_audio`), tool calls are executed one-by-one. If Gemini calls multiple tools, the latency is the sum of their run times.
*   **Goal**: Replace the sequential loop with `asyncio.gather` or a `TaskGroup` to run tool calls concurrently.

### II. Blocking CPU Wait-States in Actions (Wait-State Fix)
*   **Target**:
    *   `actions/open_app.py` has synchronous `time.sleep(1.0)` / `time.sleep(1.5)` blocks to wait for desktop apps to focus.
    *   `actions/send_message.py` has multiple seconds of `time.sleep` statements.
*   **Goal**:
    *   Change open actions to fire-and-forget: spawn the process and immediately return success without sleeping.
    *   If delay sequences are necessary, convert the functions to `async def` and use non-blocking `await asyncio.sleep`.

### III. System Status Query Delay (Metrics Cache Fix)
*   **Target**: `actions/system_monitor.py` uses `psutil.cpu_percent(interval=0.2)` inside the `get_system_status` tool. This blocks the thread for **200 milliseconds** *every single time* system metrics are requested.
*   **Goal**: Run a background daemon thread that updates a global thread-safe dict with CPU, GPU, RAM, and temp metrics every 500ms. Make `get_system_status` read from this local memory cache instantly ($<1\text{ }\mu\text{s}$) with 0ms wait-state.

### IV. Client Connection Overhead (Control Path Fix)
*   **Target**: `actions/web_search.py` creates a brand new `genai.Client` connection instance on every single execution, adding a 200–500ms TLS/TCP handshake overhead.
*   **Goal**: Instantiate a single global client and pass references (or reuse client) to preserve connection keep-alive.

### V. TTS Synthesis Pipelining (FIFO Dataflow Fix)
*   **Target**: Cloud TTS engines in `core/tts.py` (`EdgeTTS`, `ElevenLabs`) download the entire response before starting play.
*   **Goal**: Chunk responses at sentence boundaries, download chunks in parallel, and feed audio bytes into a playback queue so Aethelark starts talking within 300ms.
