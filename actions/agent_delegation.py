"""Agent delegation with one persistent session per (agent, project). The first
request starts the agent (headless JSON where supported, else a hidden PTY);
follow-ups go into the same live session.
"""

import asyncio
import re
import threading
import time

from core.agent_catalog import PREFERENCE, catalog, resolve, spoken_list
from core.agent_session import HeadlessSession, SESSION_AGENTS
from pathlib import Path

from actions.pty_session import POOL, open_viewer_terminal


class AgentAdapter:
    def __init__(self, cli):
        self.cli = cli
        self.name = cli.label
        self.registry_key = cli.key

    def _hud_pump(self, player):
        """Per-session line filter that keeps terminal-UI redraw noise out of the
        HUD: drops a line that only extends the previous one, collapses spinner
        and status lines, and drops exact repeats.
        """
        printed: set[str] = set()
        last_raw = ""
        last_status = ""

        # Spinner text comes in many shapes, so match the keyword anywhere, but only on
        # a short line with no path: work lines name a file.
        status_re = re.compile(
            r"(thinking|deliberating|generating|pondering|working|"
            r"analy[sz]ing|reading|writing|searching|running)", re.I)

        def on_line(raw_line: str):
            nonlocal last_raw, last_status
            from actions.developer_mode import clean_ansi_line
            cleaned = clean_ansi_line(raw_line)
            if not cleaned:
                return

            if last_raw and cleaned.startswith(last_raw) and len(cleaned) > len(last_raw):
                last_raw = cleaned
                return
            last_raw = cleaned

            m = status_re.search(cleaned)
            # Status lines carry suffixes like "(42s · 880 tokens · esc to interrupt)".
            if m and len(cleaned) < 80 and "/" not in cleaned:
                key = m.group(1).lower()
                if key == last_status:
                    return
                last_status = key
            else:
                last_status = ""

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
        return self.registry_key

    async def run(self, prompt: str, project_dir: Path, project_name: str,
                  player=None) -> str:
        project_dir = Path(project_dir)
        agent_key = self.pool_key

        # Follow-up turn: into the existing live session.
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

        # First turn: an agent with a streaming protocol gets a headless JSON session.
        if supports_session(agent_key):
            return await self._run_headless(prompt, project_dir, project_name,
                                            player=player)

        # Not installed: say so rather than spawn a shell that dies.
        if not self.cli.installed():
            err = (f"{self.name} is not installed on this computer, so nothing "
                   f"was started. Installed: {spoken_list()}.")
            if player:
                player.write_log(f"ERR: {err}")
            return err

        agent_cmd = self.cli.command("" if self.cli.types_prompt else prompt)

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

        # Terminal agents: a screen watcher extracts thoughts and answers recognised
        # prompts.
        try:
            from actions.agent_screen import AgentScreenWatcher
            from core.escalations import raise_escalation
            session.watcher = AgentScreenWatcher(
                session, self.name, player=player,
                # Held prompts go to the escalation registry so a human can resolve them
                # by voice.
                on_escalation=lambda agent, decision, region: raise_escalation(
                    agent, decision, region, player=player))
        except Exception as e:
            session.watcher = None
            if player:
                player.write_log(f"SYS: Screen watcher unavailable ({e}); "
                                 f"raw log streaming only.")

        await asyncio.to_thread(
            open_viewer_terminal,
            f"Aethelark Developer Console - {self.name}", session.log_path)

        if self.cli.types_prompt:
            await asyncio.to_thread(_wait_for_quiet, session)
            if session.is_alive():
                await asyncio.to_thread(session.send_line, prompt)
        else:
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


    async def _run_headless(self, prompt: str, project_dir: Path,
                            project_name: str, player=None) -> str:
        """Start a streaming session and send the first instruction as a JSON
        message, so quoting is never a bug.
        """
        agent_key = self.pool_key
        if not self.cli.installed():
            err = (f"{self.name} is not installed on this computer, so nothing "
                   f"was started. Installed: {spoken_list()}.")
            if player:
                player.write_log(f"ERR: {err}")
            return err

        if player:
            player.write_log(
                f"SYS: Starting '{self.name}' session in '{project_dir}'...")

        from core.agent_session import saved_conversation
        resume = saved_conversation(agent_key, project_dir)
        if resume and player:
            player.write_log(f"SYS: Continuing the earlier {self.name} conversation.")
        _start_idle_closer()
        try:
            session = await asyncio.to_thread(
                HeadlessSession, agent_key, self.name, project_dir,
                on_line=self._headless_pump(player), resume=resume)
        except Exception as e:
            err_msg = f"Failed to start {self.name} session: {e}"
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        POOL.adopt(agent_key, project_dir, session)
        session.player = player

        # The read-only console tails the session's transcript file.
        await asyncio.to_thread(
            open_viewer_terminal,
            f"Aethelark Developer Console - {self.name}", session.log_path)

        if not await asyncio.to_thread(session.send_line, prompt):
            session.close()
            err_msg = f"{self.name} session closed before it took the instruction."
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        # Confirm the process is still alive after startup.
        await asyncio.sleep(1.0)
        if not session.is_alive():
            tail = session.snapshot_tail(500).decode("utf-8", "replace").strip()
            err_msg = (f"{self.name} exited immediately after launch. "
                       f"Last output: {tail[-300:] or '(none)'}")
            if player:
                player.write_log(f"ERR: {err_msg}")
            return err_msg

        try:
            from actions.swarm_sentinel import SENTINEL
            SENTINEL.ensure_running()
        except Exception as _e:
            print(f"[agent_delegation.py] Non-fatal error: {_e}")
        try:
            from actions.visual_verifier import watch_directory
            watch_directory(project_dir, player=player, session=session)
        except Exception as e:
            if player:
                player.write_log(f"SYS: Visual verification unavailable ({e}).")

        if player:
            player.write_log(f"SYS: '{self.name}' session live (headless).")
        return (f"Started persistent {self.name} session for '{project_name}'. "
                f"Follow-up instructions in this directory continue the same "
                f"conversation.")

    @staticmethod
    def _headless_pump(player):
        """The headless line callback: HeadlessSession already filtered everything."""
        def on_line(text: str):
            if player:
                player.write_log(text)
            print(text)
        return on_line


_idle_closer: threading.Thread | None = None


def _start_idle_closer() -> None:
    """Close single-agent sessions that finished and went quiet. Swarm agents
    and the chief are left to the swarm."""
    global _idle_closer
    if _idle_closer is not None and _idle_closer.is_alive():
        return

    def loop() -> None:
        from actions.swarm_sentinel import swarm_root_of
        from core.agent_session import close_idle
        while True:
            time.sleep(60)
            try:
                solo = [sess for (_k, sdir), sess in POOL.all_sessions().items()
                        if swarm_root_of(sdir) is None and getattr(sess, "role", "") != "chief"]
                for sess in close_idle(solo):
                    print(f"[agent] closed idle {sess.agent_name} in {sess.project_dir.name}; "
                          f"its conversation resumes on the next request")
            except Exception as e:
                print(f"[agent] idle closer: {e}")

    _idle_closer = threading.Thread(target=loop, daemon=True, name="agent-idle-closer")
    _idle_closer.start()


def supports_session(agent_key: str) -> bool:
    """True only for agents with a captured streaming-session protocol."""
    return agent_key in SESSION_AGENTS


def spawn_succeeded(run_result: str) -> bool:
    """True only if AgentAdapter.run reported a live session (a fresh spawn or a
    routed follow-up), not merely a message containing "session".
    """
    r = (run_result or "").strip().lower()
    return r.startswith("started persistent") or r.startswith("prompt routed")


def find_session(agent_key: str, directory: str):
    """A live session for agent_key in directory: exact first, then one running under
    it (a swarm worktree).
    """
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


def latest_session(agent_key: str = ""):
    live = [(k, s) for (k, _d), s in POOL.all_sessions().items()
            if s.is_alive() and (not agent_key or k == agent_key)]
    if not live:
        return None, None
    return max(live, key=lambda ks: ks[1].created_at)


async def interject_agent(agent_key: str, directory: str = "",
                          message: str = "", player=None) -> str:
    """Stop the agent mid-generation, then give it the user's new instruction."""
    key = resolve_agent(agent_key) if agent_key else ""
    if agent_key and not key:
        return f"I don't know an agent called {agent_key}. Installed: {spoken_list()}."
    session = find_session(key, directory) if (key and directory) else None
    if session is None:
        key, session = latest_session(key)
    if not session:
        return "No coding agent is running right now, so there is nothing to stop."
    agent_key = key

    if not session.interrupt():
        if not message:
            return (f"{session.agent_name} cannot be interrupted mid-task; it "
                    f"will read a new instruction when its current step ends.")
    name = session.agent_name
    if player:
        player.write_log(f"SYS: Interrupted '{agent_key}'.")
    if message:
        await asyncio.sleep(0.7)
        try:
            sent = await asyncio.to_thread(session.send_line, message)
        except OSError:
            sent = False
        if sent is False:
            return f"{name} stopped, but did not take the new instruction. Say it again."
        if player:
            player.write_log(f"SYS: Redirected '{agent_key}': {message[:80]}")
        return f"Told {name} the new instruction."
    return f"Stopped {name}. It is waiting for instructions."


def list_active_sessions() -> dict:
    """Live sessions as {'<agent>@<dir>': seconds_alive} for HUD/telemetry."""
    import time
    return {
        f"{key[0]}@{key[1]}": round(time.time() - sess.created_at, 1)
        for key, sess in POOL.all_sessions().items() if sess.is_alive()
    }


AGENT_REGISTRY: dict[str, AgentAdapter] = {}


def refresh_registry() -> dict[str, AgentAdapter]:
    table = catalog()
    for key in list(AGENT_REGISTRY):
        if key not in table:
            del AGENT_REGISTRY[key]
    for key, cli in table.items():
        if key not in AGENT_REGISTRY or AGENT_REGISTRY[key].cli != cli:
            AGENT_REGISTRY[key] = AgentAdapter(cli)
    return AGENT_REGISTRY


refresh_registry()


def agent_available(agent_key: str) -> bool:
    a = AGENT_REGISTRY.get(agent_key)
    return bool(a and a.cli.installed())


def first_available_agent(prefer=PREFERENCE) -> str | None:
    for key in prefer:
        if agent_available(key):
            return key
    for key in AGENT_REGISTRY:
        if agent_available(key):
            return key
    return None


def resolve_agent(name: str) -> str | None:
    refresh_registry()
    return resolve(name)


def _wait_for_quiet(session, quiet_s: float = 1.5, limit_s: float = 25.0) -> None:
    """Until the freshly started TUI has drawn its input box and stopped."""
    import time
    start = time.time()
    last, since = -1, time.time()
    while time.time() - start < limit_s and session.is_alive():
        size = len(session.snapshot_tail(1 << 16))
        now = time.time()
        if size != last:
            last, since = size, now
        elif size > 0 and now - since >= quiet_s:
            return
        time.sleep(0.2)
