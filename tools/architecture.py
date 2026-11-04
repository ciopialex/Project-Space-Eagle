#!/usr/bin/env python3
"""Write ARCHITECTURE.md from the source, so the map cannot drift from the code.

    python tools/architecture.py          # rewrite ARCHITECTURE.md
    python tools/architecture.py --check  # exit 1 if it is out of date

Every table is read from the code. Every arrow in the diagram carries a string
that must appear in the file it starts from; if it is gone, generation fails
instead of drawing an edge that no longer exists.
"""
from __future__ import annotations

import ast
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "ARCHITECTURE.md"

#: (id, label, file) for every box in the diagram.
NODES = [
    ("user", "User: voice or typed", None),
    ("live", "AethelarkLive voice loop", "main.py"),
    ("gemini", "Gemini Live", None),
    ("dispatch", "_execute_tool dispatcher", "main.py"),
    ("actions", "Built-in tools", "actions/"),
    ("bus", "ModuleBus", "core/module_bus/bus.py"),
    ("modules", "Module processes: atrade, a3d", None),
    ("coding", "developer_mode / swarm_mode", "actions/developer_mode.py"),
    ("delegation", "Agent adapters", "actions/agent_delegation.py"),
    ("session", "HeadlessSession", "core/agent_session.py"),
    ("cli", "claude / agy CLI", None),
    ("ambient", "Module events watcher", "core/ambient.py"),
    ("esc", "Held requests and notes", "core/escalations.py"),
    ("confirm", "Spoken-yes gate", "core/confirm.py"),
    ("shell", "WebShellUI host", "aethelark_web.py"),
    ("pill", "Island FSM", "web/pill.html"),
    ("dash", "Dashboard", "web/dashboard.html"),
    ("onboard", "Setup", "web/onboarding.html"),
    ("data", "Data dir: keys, prefs, memory", "core/user_paths.py"),
]

#: (from, to, label, file the evidence is in, evidence).
EDGES = [
    ("user", "live", "mic frames, typed text", "main.py", "def _on_mic_frame"),
    ("live", "gemini", "audio + context", "main.py", "client.aio.live.connect"),
    ("gemini", "dispatch", "tool call", "main.py", "async def _execute_tool"),
    ("dispatch", "actions", "built-in tool", "main.py", "from actions."),
    ("dispatch", "bus", "module tool", "main.py", "MODULE_BUS.invoke"),
    ("bus", "confirm", "irreversible tool", "core/module_bus/bus.py", "_check_confirmation"),
    ("bus", "modules", "one process per call, JSON on stdout", "core/module_bus/bus.py", "subprocess.Popen"),
    ("dispatch", "shell", "module answer as a card", "main.py", "self.ui.set_pill_context"),
    ("modules", "ambient", "latest event, one JSON file per module", "aethelark_web.py", "AmbientWatcher("),
    ("ambient", "shell", "read every 2 s", "aethelark_web.py", "self._ambient_timer.start(2000)"),
    ("dispatch", "coding", "coding tool", "main.py", "from actions.developer_mode import developer_mode"),
    ("coding", "delegation", "agent.run", "actions/developer_mode.py", "agent.run("),
    ("delegation", "session", "start or resume", "actions/agent_delegation.py", "HeadlessSession"),
    ("session", "cli", "stream-json both ways", "core/agent_session.py", '"--input-format", "stream-json"'),
    ("session", "esc", "risky request held", "core/agent_session.py", "raise_escalation"),
    ("esc", "live", "HELD / DONE / NOTE turn", "main.py", "escalations.add_listener"),
    ("live", "shell", "set_state, show_notice, write_log", "main.py", "self.ui.set_state"),
    ("shell", "pill", "set_pill, run_js", "aethelark_web.py", "def set_pill"),
    ("pill", "shell", "pybridge slots", "web/pill.html", "pybridge"),
    ("shell", "dash", "window.aethelark.*", "aethelark_web.py", "window.aethelark && window.aethelark"),
    ("dash", "shell", "pybridge slots", "web/dashboard.html", "pybridge"),
    ("shell", "onboard", "window.onboarding.*", "aethelark_web.py", "window.onboarding && window.onboarding"),
    ("onboard", "shell", "pybridge slots", "web/onboarding.html", "pybridge"),
    ("shell", "data", "config, prefs", "aethelark_web.py", "user_paths."),
    ("live", "data", "memory", "main.py", "update_memory("),
]

PAGE_CALL = re.compile(r"(?:pybridge|\bbridge|\bpb\(\))\s*\.\s*(\w+)\s*\(")


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _first_line(doc: str | None) -> str:
    line = (doc or "").strip().splitlines()[0].strip() if (doc or "").strip() else ""
    return line.replace("|", "\\|")


def _check_evidence() -> list[str]:
    missing = []
    ids = {n for n, _l, _f in NODES}
    for n, _l, f in NODES:
        if f and not (ROOT / f).exists():
            missing.append(f"node {n}: {f} does not exist")
    for a, b, _label, f, ev in EDGES:
        if a not in ids or b not in ids:
            missing.append(f"edge {a}->{b}: unknown node")
        elif ev not in _read(f):
            missing.append(f"edge {a}->{b}: {f} no longer contains {ev!r}")
    return missing


def _diagram() -> str:
    lines = ["```mermaid", "flowchart LR"]
    for n, label, f in NODES:
        text = f"{label}<br/><small>{f}</small>" if f else label
        shape = ("([", "])") if f is None else ("[", "]")
        lines.append(f'  {n}{shape[0]}"{text}"{shape[1]}')
    for a, b, label, _f, _ev in EDGES:
        lines.append(f'  {a} -->|"{label}"| {b}')
    lines.append("```")
    return "\n".join(lines)


# ------------------------------------------------------------ voice tools

def _literal_names(node: ast.AST, known: dict[str, set[str]]) -> set[str]:
    if isinstance(node, ast.Call) and getattr(node.func, "id", "") in ("frozenset", "set"):
        return _literal_names(node.args[0], known) if node.args else set()
    if isinstance(node, (ast.Set, ast.List, ast.Tuple)):
        return {e.value for e in node.elts if isinstance(e, ast.Constant)}
    if isinstance(node, ast.BinOp):
        return _literal_names(node.left, known) | _literal_names(node.right, known)
    if isinstance(node, ast.Name):
        return known.get(node.id, set())
    return set()


def _voice_tools() -> list[tuple[str, str, str, str]]:
    tree = ast.parse(_read("main.py"))
    imported: dict[str, str] = {}
    sets: dict[str, set[str]] = {}
    declared: list[tuple[str, str]] = []
    execute = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for a in node.names:
                imported[a.asname or a.name] = node.module
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name in ("CODING_TOOLS", "LABS_TOOLS"):
                sets[name] = _literal_names(node.value, sets)
            if name == "TOOL_DECLARATIONS" and isinstance(node.value, ast.List):
                for d in node.value.elts:
                    if isinstance(d, ast.Dict):
                        kv = {k.value: v for k, v in zip(d.keys, d.values) if isinstance(k, ast.Constant)}
                        n = kv.get("name")
                        desc = kv.get("description")
                        text = ""
                        if isinstance(desc, ast.Constant):
                            text = desc.value
                        elif desc is not None:
                            try:
                                text = ast.literal_eval(desc)
                            except Exception:
                                text = ""
                        if isinstance(n, ast.Constant):
                            declared.append((n.value, str(text)))
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_execute_tool":
            execute = node

    handlers: dict[str, set[str]] = defaultdict(set)
    own: dict[str, set[str]] = defaultdict(set)
    if execute is not None:
        for node in ast.walk(execute):
            if not isinstance(node, ast.If):
                continue
            tool = None
            for c in ast.walk(node.test):
                if (isinstance(c, ast.Compare) and isinstance(c.left, ast.Name) and c.left.id == "name"
                        and isinstance(c.ops[0], ast.Eq) and isinstance(c.comparators[0], ast.Constant)):
                    tool = c.comparators[0].value
            if tool is None:
                continue
            for stmt in node.body:
                for c in ast.walk(stmt):
                    if isinstance(c, ast.ImportFrom) and c.module:
                        handlers[tool].add(c.module)
                    if isinstance(c, ast.Call):
                        f = c.func
                        if isinstance(f, ast.Name) and f.id in imported:
                            handlers[tool].add(imported[f.id])
                        elif isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                                and f.value.id == "self" and not f.attr.startswith(("set_", "speak")):
                            own[tool].add(f"main.AethelarkLive.{f.attr}")
    coding, labs = sets.get("CODING_TOOLS", set()), sets.get("LABS_TOOLS", set())
    rows = []
    for name, desc in declared:
        gate = "coding" if name in coding else "labs" if name in labs else "always"
        mods = {h for h in handlers.get(name, set()) if h.startswith(("actions", "core"))}
        where = ", ".join(sorted(mods or own.get(name, set()))) or "main.py"
        summary = re.split(r"(?<=[.!?])\s", desc.strip(), maxsplit=1)[0][:110]
        rows.append((name, gate, where, summary.replace("|", "\\|")))
    return rows


# -------------------------------------------------------- page <-> python

def _class_sources(rel: str) -> dict[str, ast.ClassDef]:
    tree = ast.parse(_read(rel))
    return {n.name: n for n in tree.body if isinstance(n, ast.ClassDef)}


def _slots(cls: ast.ClassDef) -> list[str]:
    out = []
    for f in cls.body:
        if isinstance(f, ast.FunctionDef) and any(
                "pyqtSlot" in ast.unparse(d) for d in f.decorator_list):
            out.append(f.name)
    return out


def _page_pairs() -> list[tuple[str, str, str]]:
    """(window class, bridge class, page) as the windows construct them."""
    src = _read("aethelark_web.py")
    consts = dict(re.findall(r'^(\w+_HTML) = BASE / "web" / "([\w.]+)"', src, re.M))
    pairs = []
    for name, cls in _class_sources("aethelark_web.py").items():
        bridges = {c.func.id for c in ast.walk(cls) if isinstance(c, ast.Call)
                   and isinstance(c.func, ast.Name) and c.func.id.endswith("Bridge")}
        pages = {n.id for n in ast.walk(cls) if isinstance(n, ast.Name) and n.id in consts}
        for b in bridges:
            for p in pages:
                pairs.append((name, b, f"web/{consts[p]}"))
    return sorted(pairs, key=lambda t: t[2])


def _js_defines(page: str, fn: str) -> bool:
    return bool(re.search(rf"\b{re.escape(fn)}\s*:\s*function|\.{re.escape(fn)}\s*=\s*function"
                          rf"|function\s+{re.escape(fn)}\s*\(", page))


def _bridges() -> list[str]:
    classes = _class_sources("aethelark_web.py")
    out = []
    for window, bridge, page in _page_pairs():
        html = _read(page)
        called = set(PAGE_CALL.findall(html))
        slots = _slots(classes[bridge])
        out.append(f"### {page}\n\nHosted by `{window}`, bridge `{bridge}` "
                   f"(`aethelark_web.py`). Page → Python slots:\n")
        out.append("| slot | called by the page |\n|---|---|")
        for s in slots:
            out.append(f"| `{s}` | {'yes' if s in called else '**no caller found**'} |")
        unknown = sorted(c for c in called if c not in slots and c not in ("then", "call"))
        if unknown:
            out.append(f"\nThe page calls these, which `{bridge}` does not define: "
                       + ", ".join(f"`{u}`" for u in unknown))
        out.append("")
    return out


def _pushes() -> list[str]:
    src = _read("aethelark_web.py")
    targets = [
        ("web/dashboard.html", "window.aethelark", set(re.findall(r'self\._push\("(\w+)"', src))
         | set(re.findall(r'dashboard\.push\("(\w+)"', src))),
        ("web/onboarding.html", "window.onboarding",
         set(re.findall(r'self\.push\("(\w+)"', src))),
        ("web/pill.html", "window.island / window.pill",
         set(re.findall(r"window\.(?:island|pill)\.(\w+)\s*\(", src))),
    ]
    out = ["| page | entry | Python calls | defined in the page |", "|---|---|---|---|"]
    for page, entry, fns in targets:
        html = _read(page)
        for fn in sorted(fns):
            out.append(f"| {page} | `{entry}` | `{fn}` | "
                       f"{'yes' if _js_defines(html, fn) else '**not found**'} |")
    return out


# ---------------------------------------------------------------- island

def _island() -> list[str]:
    html = _read("web/pill.html")
    m = re.search(r"var REST = \[(\d+), (\d+)\]", html)
    sizes = {"REST": f"{m.group(1)}×{m.group(2)}" if m else "?"}
    m = re.search(r"var CAPSULE = \[(\d+), (\d+)\]", html)
    sizes["CAPSULE"] = f"{m.group(1)}×{m.group(2)}" if m else "?"
    geom = re.search(r"var GEOM = \{(.*?)\};", html, re.S)
    states = []
    for name, val in re.findall(r"(\w+):\s*(\[\d+,\s*\d+\]|REST|CAPSULE)", geom.group(1) if geom else ""):
        size = sizes.get(val) or "×".join(re.findall(r"\d+", val))
        states.append((name, size))
    py = _read("aethelark_web.py")
    from_py = defaultdict(int)
    for s in re.findall(r'set_pill\(\s*"(\w+)"', py):
        from_py[s] += 1
    in_page = defaultdict(int)
    for s in re.findall(r'_go\(\s*"(\w+)"', html):
        in_page[s] += 1
    out = ["| state | size | `set_pill` from Python | `_go` inside the page |",
           "|---|---|---|---|"]
    for name, size in states:
        out.append(f"| `{name}` | {size} | {from_py.get(name, 0)} | {in_page.get(name, 0)} |")
    other = sorted(set(from_py) - {s for s, _ in states})
    if other:
        out.append("\nSet from Python but not in `GEOM`: " + ", ".join(f"`{o}`" for o in other))
    out.append("\nThe voice-loop states reach the island through `WebShellUI._pill_state`; "
               "module cards through `set_pill_context`.")
    return out


# --------------------------------------------------------------- modules

FIXTURES = "tests/fixtures/module_bus/manifests"


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def _tool_row(m, t) -> str:
    argv = " ".join([m.binary, *t.argv])
    before = []
    if t.confirm:
        before.append("asks the user")
    if t.danger != "safe":
        before.append(t.danger)
    if t.internal:
        before.append("host only, not offered to the model")
    limits = []
    if t.one_at_a_time:
        limits.append("one at a time")
    if t.exclusive:
        limits.append("runs alone")
    touches = ", ".join([*(f"reads {r}" for r in t.reads), *(f"writes {w}" for w in t.writes),
                         *limits])
    secs = f"{t.seconds:g}" if t.seconds is not None else "default"
    return (f"| `{t.qualified_name}` | `{_cell(argv)}` | {', '.join(before)} | {secs} "
            f"| {touches} |")


def _module_block(title: str, entry: dict, m) -> list[str]:
    out = [f"### {title} (`{m.key}`)", "",
           f"Installed from `{entry.get('source', '?')}`. Command `{m.binary}`, answers in "
           f"{m.output}. How it works inside: `ARCHITECTURE.md` in that repository.", ""]
    for a in m.accounts:
        out.append(f"- **Signs in to** `{a.site}` in the eagle's browser; the module receives "
                   f"that site's cookies on stdin through `{m.binary} {' '.join(a.receive)}`.")
    if m.events:
        prio = ", ".join(f"{k} {v}" for k, v in sorted(m.events.priority.items(),
                                                       key=lambda kv: -kv[1]))
        out.append(f"- **Speaks unprompted** through `{m.events.listener}`"
                   + (f"; event priorities: {prio}." if prio else "; no event priorities set."))
    if m.deck:
        out.append(f"- **Deck:** each candidate is refined in the background with "
                   f"`{m.key}_{m.deck.refine}`, passing its `{m.deck.key}` as `{m.deck.param}`.")
    if m.island:
        cards = ", ".join(f"`{c.name}` {c.width}×{c.height}" for c in m.island.cards)
        out.append(f"- **Island cards:** {cards}; a card is about `{m.island.about}`, opens on "
                   f"`{m.island.first}`" + (f", answers stay fresh {m.island.fresh_for:g} s"
                                             if m.island.fresh_for else "") + ".")
    elif m.island_config:
        keys = ", ".join(f"`{k}`" for k in m.island_config)
        out.append(f"- **Island:** declares {keys} but no cards, so an answer goes on screen "
                   f"when it fills a field the module's `{m.template}` uses "
                   f"(`ModuleBus.island_fields`).")
    out += ["", "| tool | runs | before it runs | seconds | touches |", "|---|---|---|---|---|"]
    out += [_tool_row(m, t) for t in m.tools]
    out.append("")
    return out


def _modules() -> list[str]:
    import tomllib
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from core.module_bus.manifest import load_manifest
    catalog = tomllib.loads(_read("core/module_bus/catalog.toml")).get("modules", {})
    found = {p.stem: load_manifest(p) for p in sorted((ROOT / FIXTURES).glob("*.toml"))}
    out = []
    listed = set()
    for name, entry in catalog.items():
        keys = [k for k in [name, *entry.get("aliases", [])] if k in found]
        if not keys:
            out.append(f"### {entry.get('title', name)}\n\nIn the catalog, but `{FIXTURES}` has "
                       f"no copy of its manifest.\n")
            continue
        listed.add(keys[0])
        out += _module_block(entry.get("title", name), entry, found[keys[0]])
    rest = sorted(set(found) - listed)
    if rest:
        out.append("Fixtures for modules not in the catalog: "
                   + ", ".join(f"`{k}`" for k in rest) + ".")
        out.append("")
    return out


# ------------------------------------------------------------ file index

def _files() -> list[str]:
    out = []
    groups = [("Top level", sorted(ROOT.glob("*.py"))),
              ("actions/", sorted((ROOT / "actions").rglob("*.py"))),
              ("core/", sorted((ROOT / "core").rglob("*.py"))),
              ("tools/", sorted((ROOT / "tools").glob("*.py")))]
    for title, paths in groups:
        out.append(f"### {title}\n\n| file | says it is |\n|---|---|")
        for p in paths:
            if "__pycache__" in p.parts or p.name == "__init__.py":
                continue
            text = p.read_text(encoding="utf-8", errors="replace")
            try:
                doc = ast.get_docstring(ast.parse(text))
            except SyntaxError:
                doc = None
            out.append(f"| `{p.relative_to(ROOT)}` | {_first_line(doc)} |")
        out.append("")
    return out


def _stores() -> list[str]:
    tree = ast.parse(_read("core/user_paths.py"))
    out = ["| function | says |", "|---|---|"]
    for f in tree.body:
        if isinstance(f, ast.FunctionDef) and not f.name.startswith("_"):
            out.append(f"| `user_paths.{f.name}()` | {_first_line(ast.get_docstring(f))} |")
    return out


def generate() -> str:
    missing = _check_evidence()
    if missing:
        raise SystemExit("The diagram no longer matches the code:\n  " + "\n  ".join(missing))
    tools = _voice_tools()
    parts = [
        "# Architecture",
        "",
        "Generated by `tools/architecture.py` from the source. Do not edit by hand: "
        "run `python tools/architecture.py`. `tests/test_architecture_map_is_current.py` "
        "fails when this file and the code disagree.",
        "",
        "## Data flow",
        "",
        "Every arrow is checked: the file it starts from must still contain the "
        "evidence string listed under it in `tools/architecture.py`.",
        "",
        _diagram(),
        "",
        f"## Voice tools ({len(tools)})",
        "",
        "Declared in `main.py` `TOOL_DECLARATIONS`, run by `AethelarkLive._execute_tool`. "
        "`coding` tools need CODING mode, `labs` tools the Labs switch "
        "(`main.tool_enabled`). Module tools are added at runtime from each "
        "installed module's manifest and run through `ModuleBus.invoke`.",
        "",
        "| tool | gate | handled in | first sentence of its description |",
        "|---|---|---|---|",
        *[f"| `{n}` | {g} | {w} | {d} |" for n, g, w, d in tools],
        "",
        "## Modules",
        "",
        "The modules `eagle install` knows by name (`core/module_bus/catalog.toml`), read "
        f"from the copies of their manifests in `{FIXTURES}`. A tool is offered to the model "
        "as `<key>_<name>` and runs as one process: the command line below, with the "
        "call's arguments filled in. When a module's manifest changes, copy it over its "
        "fixture and regenerate.",
        "",
        *_modules(),
        "## Island states",
        "",
        "From `GEOM` in `web/pill.html`. Rules for the state machine are in `CLAUDE.md`.",
        "",
        *_island(),
        "",
        "## Python → page calls",
        "",
        *_pushes(),
        "",
        "## Page → Python slots",
        "",
        *_bridges(),
        "## Where data lives",
        "",
        *_stores(),
        "",
        "## Files",
        "",
        *_files(),
    ]
    return "\n".join(parts).rstrip() + "\n"


def main() -> int:
    text = generate()
    if "--check" in sys.argv:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print("ARCHITECTURE.md is out of date: run python tools/architecture.py")
            return 1
        return 0
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({text.count(chr(10))} lines)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
