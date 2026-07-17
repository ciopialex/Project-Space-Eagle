```mermaid
graph TB
    subgraph Hardware & OS Layer
        Mic["🎤 Hardware Mic Input"]
        Spk["🔊 Hardware Speaker Output"]
        Cam["📷 Webcam Device"]
        Disp["🖥️ Screen Frame Buffer"]
        Disk["💾 Disk (Config/Logs/Memory DB)"]
        OS_Proc["⚙️ OS Processes & Subprocesses"]
        OS_Kern["🧠 Kernel Resource Metrics (CPU/RAM/GPU/Temp)"]
    end

    subgraph PyQt6 UI Thread
        JarvisUI["JarvisUI (Main GUI Event Loop)"]
        Pill["PillWidget (Floating Island overlay)"]
        Logs["LogWidget (Chat & System event logger)"]
        CamStream["HUD Camera Stream Panel"]
    end

    subgraph asyncio Event Loop (main.py Engine Thread)
        direction TB
        
        subgraph Upstream Audio Pipeline
            InputStream["sounddevice.InputStream (16kHz PCM)"]
            InputStreamCallback["Callback Function (Thread-safe context)"]
            OutQueue["out_queue (asyncio.Queue, maxsize=200)"]
            SendRealtimeTask["_send_realtime (Async Task Loop)"]
        end

        subgraph Downstream Audio Pipeline
            RecvTask["_receive_audio (Async Task Loop)"]
            AudioInQueue["audio_in_queue (asyncio.Queue)"]
            PlayTask["_play_audio (Async Task Loop)"]
            RawOutputStream["sounddevice.RawOutputStream (24kHz PCM)"]
        end

        subgraph Sequential Tool execution Loop
            ToolCallHandler["Tool Call Handler (Sequential iteration)"]
            ExecTool["_execute_tool (Method dispatcher)"]
            ThreadPoolExecutor["ThreadPoolExecutor (Global Thread Pool)"]
            
            subgraph Synchronous Blocking Actions
                OpenApp["open_app() Action"]
                SysStatus["get_system_status() Action"]
                ScreenProc["screen_process() Action"]
                SaveMem["save_memory() Action"]
            end
        end

        subgraph Background Engine Tasks
            SysMonTask["_run_system_monitor (10s polling loop)"]
            ProactiveTask["_run_proactive_mode (60s check-in loop)"]
            DashboardServer["DashboardServer (FastAPI/Uvicorn WebSocket Host)"]
        end
    end

    subgraph Remote Cloud Services
        GeminiLiveAPI["Gemini 2.5 Multimodal Live WebSocket Session"]
    end

    Mic --> InputStream
    InputStream --> InputStreamCallback
    InputStreamCallback --> OutQueue
    OutQueue --> SendRealtimeTask
    SendRealtimeTask --> GeminiLiveAPI

    GeminiLiveAPI --> RecvTask
    RecvTask --> AudioInQueue
    AudioInQueue --> PlayTask
    PlayTask --> RawOutputStream
    RawOutputStream --> Spk

    RecvTask --> Logs
    RecvTask --> Pill
    PlayTask --> Pill

    RecvTask --> ToolCallHandler
    ToolCallHandler --> ExecTool
    ExecTool --> ThreadPoolExecutor
    
    ThreadPoolExecutor --> OpenApp
    OpenApp --> OS_Proc
    
    ThreadPoolExecutor --> SysStatus
    SysStatus --> OS_Kern
    
    ThreadPoolExecutor --> ScreenProc
    ScreenProc --> Cam
    ScreenProc --> Disp
    ScreenProc --> CamStream
    
    ExecTool --> Disk
    SaveMem --> Disk

    OpenApp --> ExecTool
    SysStatus --> ExecTool
    ScreenProc --> ExecTool
    
    ExecTool --> ToolCallHandler
    
    ToolCallHandler --> GeminiLiveAPI

    SysMonTask --> OS_Kern
    SysMonTask --> GeminiLiveAPI
    
    ProactiveTask --> Disk
    ProactiveTask --> GeminiLiveAPI
    
    DashboardServer --> Logs
```
