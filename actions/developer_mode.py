import asyncio
import os
import subprocess
import re
from pathlib import Path

# Regex to strip ANSI escape codes (color codes, cursor movements, etc.)
ANSI_ESCAPE = re.compile(r'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')

def clean_ansi_line(line: str) -> str:
    cleaned = ANSI_ESCAPE.sub('', line)
    cleaned = cleaned.replace('\r', '').replace('\x08', '').strip()
    return cleaned

async def developer_mode(parameters: dict, player=None) -> str:
    project_name = parameters.get("project_name", "spotify_clone").strip()
    prompt = parameters.get("prompt", "Make a Spotify Clone").strip()
    directory = parameters.get("directory", "").strip()

    # Determine project directory
    space_eagle_dir = Path("/home/shennyonthebeat/Projects/Space-Eagle").resolve()

    if directory:
        # User specified an explicit path
        project_dir = Path(directory).expanduser().resolve()
    else:
        # Check if project folder already exists in standard locations
        candidates = [
            Path.home() / "Projects" / project_name,
            Path.home() / "Desktop" / project_name
        ]
        
        project_dir = None
        for cand in candidates:
            if cand.exists() and cand.is_dir():
                project_dir = cand
                break
                
        if not project_dir:
            msg = "SYS: In which folder should I focus on the goal you want to reach?"
            if player:
                player.write_log(f"SYS: Target folder of project '{project_name}' not specified or detected.")
                player.write_log(msg)
            print(msg)
            return f"Ask: {msg}"

    # Space-Eagle Codebase Safeguard
    try:
        # Check if project_dir is inside or equal to Space-Eagle
        is_safe = True
        if project_dir.resolve() == space_eagle_dir:
            is_safe = False
        elif space_eagle_dir in project_dir.resolve().parents:
            is_safe = False
            
        if not is_safe:
            msg = "ERR: Space-Eagle safeguard blocked modification of Aethelark's own directory."
            if player:
                player.write_log(msg)
                player.write_log("SYS: Action blocked. You cannot run Developer Mode inside Aethelark's installation.")
                player.write_log("SYS: Where is that directory you want for us to start the development journey in?")
            print(msg)
            return "Blocked: Space-Eagle safeguard. Please specify a different folder target."
    except Exception as e:
        pass

    # Ensure project directory exists
    try:
        project_dir.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        err_msg = f"Failed to create directory {project_dir}: {e}"
        if player:
            player.write_log(f"ERR: {err_msg}")
        return err_msg

    # Clean old logs
    log_path = Path(f"/tmp/agy_developer_mode_{project_name}.log")
    if log_path.exists():
        try:
            log_path.unlink()
        except Exception:
            pass

    # Escape prompt for single quotes in bash
    esc_prompt = prompt.replace("'", "'\\''")
    
    # We use the 'script' utility to force flush PTY output into the log file in real-time
    bash_command = (
        f"script -f -q -c \"agy -i '{esc_prompt}' --dangerously-skip-permissions\" {log_path}; exec bash"
    )

    cmd = [
        "gnome-terminal",
        "--title=Aethelark Developer Console",
        f"--working-directory={project_dir}",
        "--",
        "bash", "-c",
        bash_command
    ]

    if player:
        player.write_log(f"SYS: Entering Developer Mode in '{project_dir}'...")
        player.write_log(f"SYS: Spawning terminal and starting Antigravity CLI...")

    try:
        subprocess.Popen(cmd)
    except Exception as e:
        err_msg = f"Failed to spawn terminal: {e}"
        if player:
            player.write_log(f"ERR: {err_msg}")
        return err_msg

    # Wait for the log file to be created
    for _ in range(20):
        if log_path.exists():
            break
        await asyncio.sleep(0.5)

    if not log_path.exists():
        err_msg = "Log file was not created. Terminal or agy may have failed to start."
        if player:
            player.write_log(f"ERR: {err_msg}")
        return err_msg

    # Tail the log file
    if player:
        player.write_log("SYS: Antigravity CLI stream connected. Monitoring progress...")

    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            f.seek(0)
            printed_lines = set()
            
            while True:
                line = f.readline()
                if line:
                    cleaned = clean_ansi_line(line)
                    if cleaned and cleaned not in printed_lines:
                        if len(cleaned) > 3:
                            printed_lines.add(cleaned)
                            if len(printed_lines) > 500:
                                printed_lines.clear()
                                
                        if player:
                            player.write_log(f"[DevConsole] {cleaned}")
                        print(f"[DevConsole] {cleaned}")
                else:
                    await asyncio.sleep(0.2)
    except asyncio.CancelledError:
        if player:
            player.write_log("SYS: Developer Mode task cancelled.")
        raise
    except Exception as e:
        err_msg = f"Log tailing error: {e}"
        if player:
            player.write_log(f"ERR: {err_msg}")
        return err_msg

    return "Done"
