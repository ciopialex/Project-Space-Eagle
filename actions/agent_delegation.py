"""Agent delegation with persistent single-session memory.

First delegation to (agent, project_dir) spawns the agent CLI on a hidden
persistent PTY (plus one read-only viewer terminal). Follow-up prompts in
the same directory are typed straight into the live session — no duplicate
windows, no lost conversation history.
"""

import asyncio
from pathlib import Path

from actions.pty_session import POOL, open_viewer_terminal


class AgentAdapter:
    def __init__(self, name: str, command_template: str):
        self.name = name
        self.command_template = command_template

    def _hud_pump(self, player):
        """Build a per-session line callback that dedupes noise for the HUD."""
        printed = set()

        def on_line(raw_line: str):
            from actions.developer_mode import clean_ansi_line
            cleaned = clean_ansi_line(raw_line)
            if not cleaned or cleaned in printed:
                return
            if len(cleaned) > 3:
                printed.add(cleaned)
                if len(printed) > 500:
                    printed.clear()
            if player:
                player.write_log(f"[{self.name}] {cleaned}")
            print(f"[{self.name}] {cleaned}")

        return on_line

    async def run(self, prompt: str, project_dir: Path, project_name: str,
                  player=None) -> str:
        project_dir = Path(project_dir)
        agent_key = self.name.lower()

        # ---- Follow-up turn: route into the existing live session ----------
        session = POOL.get_alive(agent_key, project_dir)
        if session:
            if player:
                player.write_log(
                    f"SYS: Continuing active '{self.name}' session in "
                    f"'{project_dir.name}' (no new window).")
            try:
                await asyncio.to_thread(session.send_line, prompt)
            except OSError as e:
                if player:
                    player.write_log(f"ERR: Session write failed: {e}")
                return f"Session write failed: {e}"
            return (f"Prompt routed into the active {self.name} session for "
                    f"'{project_name}'. It will respond in its console.")

        # ---- First turn: spawn a fresh persistent session ------------------
        esc_prompt = prompt.replace("'", "'\\''")
        agent_cmd = self.command_template.format(prompt=esc_prompt)

        if player:
            player.write_log(
                f"SYS: Starting persistent '{self.name}' session in "
                f"'{project_dir}'...")

        try:
            session = await asyncio.to_thread(
                POOL.create, agent_key, self.name, agent_cmd, project_dir,
                self._hud_pump(player))
        except Exception as e:
            err_msg = f"Failed to start {self.name} session: {e}"
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        # One read-only live console per session (best effort).
        await asyncio.to_thread(
            open_viewer_terminal,
            f"Aethelark Developer Console - {self.name}", session.log_path)

        # Confirm the process survived startup.
        await asyncio.sleep(1.0)
        if not session.is_alive():
            tail = session.snapshot_tail(500).decode("utf-8", "replace").strip()
            err_msg = (f"{self.name} exited immediately after launch. "
                       f"Last output: {tail[-300:] or '(none)'}")
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        if player:
            player.write_log(
                f"SYS: '{self.name}' session live. Output streams here and in "
                f"its console window.")
        return (f"Started persistent {self.name} session for '{project_name}'. "
                f"Follow-up instructions in this directory continue the same "
                f"conversation.")


def interrupt_agent(agent_name: str, project_dir: str) -> str:
    """Send Ctrl+C into a live agent session (voice interjection hook)."""
    session = POOL.get_alive(agent_name.lower(), Path(project_dir))
    if not session:
        return f"No active {agent_name} session in {project_dir}."
    return ("Interrupt sent." if session.interrupt()
            else "Interrupt failed: session pipe closed.")


def list_active_sessions() -> dict:
    """Live sessions as {'<agent>@<dir>': seconds_alive} for HUD/telemetry."""
    import time
    return {
        f"{key[0]}@{key[1]}": round(time.time() - sess.created_at, 1)
        for key, sess in POOL.all_sessions().items() if sess.is_alive()
    }


# Registry of available agents for delegation
AGENT_REGISTRY = {
    "antigravity_cli": AgentAdapter("AntigravityCLI", "agy -i '{prompt}'"),
    "antigravity_ide": AgentAdapter("AntigravityIDE", "agy ide -i '{prompt}'"),
    "claude_code": AgentAdapter("ClaudeCode", "claude '{prompt}'"),
    "kimi": AgentAdapter("Kimi", "kimi -i '{prompt}'"),
    "opencode": AgentAdapter("OpenCode", "opencode -i '{prompt}'"),
}
