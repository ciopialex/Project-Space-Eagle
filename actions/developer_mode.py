import re
from pathlib import Path

ANSI_ESCAPE = re.compile(r'(?:\x1B[@-_]|[\x80-\x9F])[0-?]*[ -/]*[@-~]')

SPACE_EAGLE_HOME = Path(__file__).resolve().parent.parent
SEARCH_ROOTS = ("Projects", "projects", "code", "Code", "src", "dev", "Desktop", "")


def clean_ansi_line(line: str) -> str:
    cleaned = ANSI_ESCAPE.sub('', line)
    return cleaned.replace('\r', '').replace('\x08', '').strip()


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


def find_project(name: str) -> Path | None:
    wanted = _slug(name)
    if not wanted:
        return None
    home = Path.home()
    for root in SEARCH_ROOTS:
        base = home / root if root else home
        if not base.is_dir():
            continue
        try:
            for child in base.iterdir():
                if child.is_dir() and _slug(child.name) == wanted:
                    return child
        except OSError:
            continue
    return None


def _inside_eagle(path: Path) -> bool:
    p = path.resolve()
    return p == SPACE_EAGLE_HOME or SPACE_EAGLE_HOME in p.parents


def _live_agent_in(project_dir: Path) -> str | None:
    from actions.pty_session import POOL
    target = str(project_dir.resolve())
    for (key, sdir), sess in POOL.all_sessions().items():
        if sdir == target and sess.is_alive():
            return key
    return None


async def developer_mode(parameters: dict, player=None) -> str:
    from actions.agent_delegation import (AGENT_REGISTRY, first_available_agent,
                                          resolve_agent)
    from core.agent_catalog import spoken_list

    prompt = (parameters.get("prompt") or "").strip()
    if not prompt:
        return "Ask: What should the coding agent do?"
    name = (parameters.get("project_name") or "").strip()
    directory = (parameters.get("directory") or "").strip()

    if directory:
        project_dir = Path(directory).expanduser()
    else:
        project_dir = find_project(name) or (Path.home() / "Projects" / (_slug(name) or "scratch"))
    project_dir = project_dir.resolve()
    if _inside_eagle(project_dir):
        return ("Blocked: coding agents never work inside the eagle's own "
                "folder. Name a different project.")
    created = not project_dir.exists()
    project_dir.mkdir(parents=True, exist_ok=True)

    asked = (parameters.get("agent") or "").strip()
    if asked:
        agent_key = resolve_agent(asked)
        if not agent_key or not AGENT_REGISTRY[agent_key].cli.installed():
            return (f"{asked} is not installed on this computer. Installed: "
                    f"{spoken_list()}. Ask the user which of those to use.")
    else:
        agent_key = _live_agent_in(project_dir) or first_available_agent()
    if not agent_key:
        return ("No coding agent is installed on this computer. Tell the user "
                "to install one, for example Claude Code or Gemini CLI.")

    from actions.agent_delegation import spawn_succeeded
    agent = AGENT_REGISTRY[agent_key]
    result = await agent.run(prompt=prompt, project_dir=project_dir,
                             project_name=project_dir.name, player=player)
    if not spawn_succeeded(result):
        return result
    followup = result.lower().startswith("prompt routed")
    where = f"a new folder called {project_dir.name}" if created else project_dir.name
    return (f"{agent.name} {'has the new instruction' if followup else 'is working on it'} "
            f"in {where}. It shows on the island and says when it is done. Tell the "
            f"user in one short sentence.")
