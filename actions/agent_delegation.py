import asyncio
import os
import subprocess
from pathlib import Path

class AgentAdapter:
    def __init__(self, name: str, command_template: str):
        self.name = name
        self.command_template = command_template

    async def run(self, prompt: str, project_dir: Path, project_name: str, player=None) -> str:
        # Clean old logs
        log_path = Path(f"/tmp/agent_delegation_{self.name}_{project_name}.log")
        if log_path.exists():
            try:
                log_path.unlink()
            except Exception:
                pass

        esc_prompt = prompt.replace("'", "'\\''")
        # Format the command template with prompt
        agent_cmd = self.command_template.format(prompt=esc_prompt)
        
        # We use 'script' to capture pty outputs for real-time tailing
        bash_command = f"script -f -q -c \"{agent_cmd}\" {log_path}; exec bash"
        
        cmd = [
            "gnome-terminal",
            f"--title=Aethelark Developer Console - {self.name}",
            f"--working-directory={project_dir}",
            "--",
            "bash", "-c",
            bash_command
        ]
        
        if player:
            player.write_log(f"SYS: Delegating to agent '{self.name}' in '{project_dir}'...")
            player.write_log(f"SYS: Spawning terminal and starting execution...")

        try:
            subprocess.Popen(cmd)
        except Exception as e:
            err_msg = f"Failed to spawn terminal for {self.name}: {e}"
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        # Wait for the log file to be created
        for _ in range(20):
            if log_path.exists():
                break
            await asyncio.sleep(0.5)

        if not log_path.exists():
            err_msg = f"Log file was not created. Terminal or {self.name} may have failed to start."
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        # Tail the log file
        from actions.developer_mode import clean_ansi_line
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
                                player.write_log(f"[{self.name}] {cleaned}")
                            print(f"[{self.name}] {cleaned}")
                    else:
                        await asyncio.sleep(0.2)
        except asyncio.CancelledError:
            if player:
                player.write_log(f"SYS: Agent '{self.name}' task cancelled.")
            raise
        except Exception as e:
            err_msg = f"Log tailing error: {e}"
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        return "Done"

# Registry of available agents for delegation
AGENT_REGISTRY = {
    "antigravity_cli": AgentAdapter("AntigravityCLI", "agy -i '{prompt}'"),
    "antigravity_ide": AgentAdapter("AntigravityIDE", "agy ide -i '{prompt}'"),
    "claude_code": AgentAdapter("ClaudeCode", "claude '{prompt}'"),
    "kimi": AgentAdapter("Kimi", "kimi -i '{prompt}'"),
    "opencode": AgentAdapter("OpenCode", "opencode -i '{prompt}'"),
}
