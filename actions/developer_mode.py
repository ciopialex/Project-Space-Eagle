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
    agent_key = parameters.get("agent", "antigravity_cli").strip().lower()
    from actions.agent_delegation import AGENT_REGISTRY
    agent = AGENT_REGISTRY.get(agent_key)
    if not agent:
        err_msg = f"Unknown agent: {agent_key}. Available: {list(AGENT_REGISTRY.keys())}"
        if player:
            player.write_log(f"ERR: {err_msg}")
        return err_msg
        
    return await agent.run(prompt=prompt, project_dir=project_dir, project_name=project_name, player=player)
