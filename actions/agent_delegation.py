"""Agent delegation with persistent single-session memory.

First delegation to (agent, project_dir) spawns the agent CLI on a hidden
persistent PTY (plus one read-only viewer terminal). Follow-up prompts in
the same directory are typed straight into the live session — no duplicate
windows, no lost conversation history.
"""

import asyncio
import re
from pathlib import Path

from actions.pty_session import POOL, open_viewer_terminal


class AgentAdapter:
    def __init__(self, name: str, command_template: str):
        self.name = name
        self.command_template = command_template

    def _hud_pump(self, player):
        """Per-session line callback that keeps TUI redraw noise out of the HUD.

        Agent CLIs are full-screen TUIs: they repaint a status line constantly,
        and the PTY emits every repaint as another "line". Exact-match deduping
        cannot catch that, because each repaint is a genuinely different string:

            Gen  /  Gene  /  Gener  /  Generating…
            1thinking  /  2thinking  /  ✱thinking  /  ✻still thinking

        which is why the log filled with hundreds of near-identical entries and
        the UI showed a wall of `[ClaudeCode] 3thinking`. Three filters, in
        order of how much they catch:

          1. progressive redraw — a line that merely extends the previous one
             character by character is the same line being typed out;
          2. status/spinner lines — normalised (leading glyphs, counters and
             elapsed times removed) before deduping, so "thinking" reports
             collapse to one entry instead of one per frame;
          3. exact repeats, as before.
        """
        printed: set[str] = set()
        last_raw = ""
        last_status = ""

        # Spinner text arrives in many shapes — "1thinking", "✱still thinking",
        # "10s · thinking)", "Deliberating… (42s · 880 tokens)" — so anchoring
        # the pattern misses most of them. Match the keyword anywhere, but only
        # treat it as status when the line is SHORT and carries no path: real
        # work lines name a file, status lines never do.
        status_re = re.compile(
            r"(thinking|deliberating|generating|pondering|working|"
            r"analy[sz]ing|reading|writing|searching|running)", re.I)

        def on_line(raw_line: str):
            nonlocal last_raw, last_status
            from actions.developer_mode import clean_ansi_line
            cleaned = clean_ansi_line(raw_line)
            if not cleaned:
                return

            # 1. the same line still being typed out
            if last_raw and cleaned.startswith(last_raw) and len(cleaned) > len(last_raw):
                last_raw = cleaned
                return
            last_raw = cleaned

            # 2. spinner/status churn — keep the first, drop the repaints
            m = status_re.search(cleaned)
            # 80 rather than something tighter: status lines carry suffixes
            # like "(42s · 880 tokens · esc to interrupt)". Real work lines are
            # already excluded by the path check, so length can be generous.
            if m and len(cleaned) < 80 and "/" not in cleaned:
                key = m.group(1).lower()
                if key == last_status:
                    return
                last_status = key
            else:
                last_status = ""

            # 3. exact repeats
            if cleaned in printed:
                return
            if len(cleaned) > 3:
                printed.add(cleaned)
                if len(printed) > 500:
                    printed.clear()

            if player:
                player.write_log(f"[{self.name}] {cleaned}")
            print(f"[{self.name}] {cleaned}")

        return on_line

    @property
    def pool_key(self) -> str:
        return getattr(self, "registry_key", self.name.lower())

    async def run(self, prompt: str, project_dir: Path, project_name: str,
                  player=None) -> str:
        project_dir = Path(project_dir)
        agent_key = self.pool_key

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
        # Fail HONESTLY if the agent CLI isn't installed, instead of spawning a
        # shell that dies and reporting a confusing "exited immediately". This is
        # the common cause of "it said it started but nothing ran".
        import shutil as _shutil
        _binary = self.command_template.split()[0]
        if _shutil.which(_binary) is None:
            err = (f"The {self.name} CLI ('{_binary}') isn't installed or on PATH — "
                   f"NOTHING was started. Install it, or ask me to use an agent "
                   f"that is installed.")
            if player:
                player.write_log(f"ERR: {err}")
            return err

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

        # Virtual VT100 watcher: thought extraction + prompt auto-approval.
        try:
            from actions.agent_screen import AgentScreenWatcher
            from core.escalations import raise_escalation
            session.watcher = AgentScreenWatcher(
                session, self.name, player=player,
                # A refusal to auto-answer is only half a system: without a
                # destination the agent blocks forever. Route held prompts to
                # the pending-escalation registry so a human can actually
                # resolve them by voice.
                on_escalation=lambda agent, decision, region: raise_escalation(
                    agent, decision, region, player=player))
        except Exception as e:
            session.watcher = None
            if player:
                player.write_log(f"SYS: Screen watcher unavailable ({e}); "
                                 f"raw log streaming only.")

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

        session.player = player
        try:
            from actions.swarm_sentinel import SENTINEL
            SENTINEL.ensure_running()
        except Exception as _e:
            print(f"[agent_delegation.py] Non-fatal error at line 112: {_e}")
        try:
            from actions.visual_verifier import watch_directory
            watch_directory(project_dir, player=player, session=session)
        except Exception as e:
            if player:
                player.write_log(f"SYS: Visual verification unavailable ({e}).")

        if player:
            player.write_log(
                f"SYS: '{self.name}' session live. Output streams here and in "
                f"its console window.")
        return (f"Started persistent {self.name} session for '{project_name}'. "
                f"Follow-up instructions in this directory continue the same "
                f"conversation.")


def spawn_succeeded(run_result: str) -> bool:
    """True iff AgentAdapter.run reported a LIVE session — a fresh spawn
    ('Started persistent …') or a routed follow-up ('Prompt routed …').

    Guards against failure messages that merely contain the word 'session'
    (e.g. 'Failed to start X session: …', 'Session write failed: …'), which a
    naive `'session' in result` test would misread as success — the classic
    'the tool said it worked but nothing ran' bug.
    """
    r = (run_result or "").strip().lower()
    return r.startswith("started persistent") or r.startswith("prompt routed")


def find_session(agent_key: str, directory: str):
    """Locate a live session for agent_key in directory — direct hit first,
    then any session running under it (e.g. a swarm worktree)."""
    root = Path(directory).expanduser().resolve()
    session = POOL.get_alive(agent_key, root)
    if session:
        return session
    for (key, sdir), sess in POOL.all_sessions().items():
        if key == agent_key and sess.is_alive():
            sdir_path = Path(sdir)
            if root == sdir_path or root in sdir_path.parents:
                return sess
    return None


async def interject_agent(agent_key: str, directory: str,
                          message: str = "", player=None) -> str:
    """Voice interjection: Ctrl+C the agent mid-generation, then pipe the
    user's new instruction straight into its session."""
    session = find_session(agent_key, directory)
    if not session:
        active = ", ".join(list_active_sessions()) or "none"
        return f"No live '{agent_key}' session under {directory}. Active: {active}"

    if not session.interrupt():
        return f"Could not interrupt {agent_key}: session pipe closed."
    if player:
        player.write_log(f"SYS: Interrupted '{agent_key}' (Ctrl+C).")
    if message:
        await asyncio.sleep(0.7)  # let the CLI settle back to its input box
        try:
            await asyncio.to_thread(session.send_line, message)
        except OSError as e:
            return f"Interrupted, but redirect failed: {e}"
        if player:
            player.write_log(f"SYS: Redirected '{agent_key}': {message[:80]}")
        return f"Interrupted {agent_key} and injected the new instruction."
    return f"Interrupted {agent_key}. It is now waiting for instructions."


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

def agent_available(agent_key: str) -> bool:
    a = AGENT_REGISTRY.get(agent_key)
    import shutil
    return bool(a and shutil.which(a.command_template.split()[0]))


def first_available_agent(
    prefer=("claude_code", "antigravity_cli", "opencode", "kimi")) -> str | None:
    """The best INSTALLED agent CLI, in preference order — so 'start development'
    picks something that actually exists instead of a default that isn't installed."""
    for key in prefer:
        if agent_available(key):
            return key
    for key in AGENT_REGISTRY:
        if agent_available(key):
            return key
    return None


AGENT_ALIASES = {
    "antigravity": "antigravity_cli",
    "anti-gravity": "antigravity_cli",
    "agy": "antigravity_cli",
    "antigravity_cli": "antigravity_cli",
    "claude": "claude_code",
    "cloud": "claude_code",
    "clawed": "claude_code",
    "claude_code": "claude_code",
    "opencode": "opencode",
    "open_code": "opencode",
    "kimi": "kimi",
}

for _key, _adapter in AGENT_REGISTRY.items():
    _adapter.registry_key = _key
