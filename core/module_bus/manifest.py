"""What a module says it can do, and how that becomes an argv.

A manifest is TOML because a human writes it by hand next to a CLI they did not
write, and because it must be readable by someone auditing what the eagle is
allowed to run on their machine.

    key         = "a3d"          # namespace; tools become a3d_<name>
    binary      = "a3d"          # resolved on PATH, never a shell string
    description = "…"            # one line, shown to the model
    output      = "text"|"json"  # how to read stdout. Default "text".

    [[tools]]
    name        = "search"
    description = "…"            # this is the model's only documentation
    argv        = ["search", "{query}", "--limit={limit}"]

    [tools.params.query]
    type        = "STRING"
    description = "…"
    required    = true
"""
from __future__ import annotations

import re

import json

import tomllib
from dataclasses import dataclass, field
import tempfile
from pathlib import Path, PurePath
from typing import Any, Iterable

# Gemini's parameter types. Anything else would make the whole session fail to connect.
VALID_TYPES = {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT"}


@dataclass(frozen=True)
class ModuleParam:
    name: str
    type: str = "STRING"
    description: str = ""
    required: bool = False
    # What the module does when the argument is left out.
    default: Any = None

    def schema(self) -> dict[str, Any]:
        return {"type": self.type, "description": self.description}


# A module key is an identifier: it becomes a directory, a Gemini function name, an HTML
# attribute value and a filename.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)

# Reserved for the confirmation handshake; stripped before argv is built.
from core.confirm import PARAM as CONFIRM_PARAM  # noqa: E402

# How undoable a tool is, in core/capabilities.py's vocabulary.
SAFE, UNDOABLE, PERMANENT = "safe", "undoable", "permanent"
DANGERS = (SAFE, UNDOABLE, PERMANENT)

# Resources the scheduler arbitrates over. Anything else would read as protected and not
# be.
RESOURCES = frozenset({
    "net", "web", "file", "files", "printer", "desktop",
    "system", "memory", "camera", "island",
})


@dataclass(frozen=True)
class ModuleTool:
    module_key: str
    name: str
    description: str
    argv: tuple[str, ...]
    params: tuple[ModuleParam, ...] = ()
    # How long this tool really takes, measured by the module. None means undeclared:
    # the host picks a conservative default, never unlimited.
    seconds: float | None = None
    # Unsafe to run twice concurrently. Narrower than `exclusive`, which blocks the
    # whole tool pool.
    one_at_a_time: bool | None = None
    # safe | undoable | permanent. `permanent` requires a confirm_prompt (validator).
    danger: str = SAFE
    # Harness resources this tool reads and writes: net, web, file, files, printer,
    # desktop, system, memory, camera, island. Two tools run together only when neither
    # writes what the other touches.
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    # Nothing else in the process may run alongside this; it freezes the whole tool
    # pool. Prefer one_at_a_time or writes.
    exclusive: bool = False
    # Arguments the host fills in, not the model: `_selection` is what the user picked
    # on the live card, so the confirmation digest covers the screen.
    injects: tuple[str, ...] = ()
    # A human must say yes before it runs.
    confirm: bool = False
    # The consequence, in the words the eagle speaks. {placeholders} come from the
    # call's arguments.
    confirm_prompt: str = ""
    # Invokable by the harness, never offered to the model.
    internal: bool = False

    @property
    def qualified_name(self) -> str:
        """`a3d_search`, not `search`, so module tools cannot collide with core tool
        names.
        """
        return f"{self.module_key}_{self.name}"

    @property
    def required(self) -> list[str]:
        return [p.name for p in self.params if p.required]

    def declaration(self) -> dict[str, Any]:
        """The function declaration, in the shape `TOOL_DECLARATIONS` uses."""
        parameters: dict[str, Any] = {
            "type": "OBJECT",
            "properties": {p.name: p.schema() for p in self.params},
        }
        # Omitted when nothing is required, rather than [].
        if self.required:
            parameters["required"] = self.required

        description = self.description
        if self.confirm:
            # The model needs a slot for the token and to know the first call comes back
            # with a question.
            parameters["properties"][CONFIRM_PARAM] = {
                "type": "STRING",
                "description": ("Leave EMPTY on the first call. This tool asks "
                                "for confirmation first; speak the question it "
                                "returns to the user, and only if they agree, "
                                "call again passing the token it gave you."),
            }
            description = (f"{description} REQUIRES SPOKEN CONFIRMATION: the "
                           f"first call returns a question to ask the user.")

        return {"name": self.qualified_name,
                "description": description,
                "parameters": parameters}


def island_event_dir() -> Path:
    """Where a module's island events live, private to this user: $XDG_RUNTIME_DIR on
    Linux, else the temp directory. Host and modules compute it the same way.
    """
    import os
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime)
    return Path(tempfile.gettempdir())


@dataclass(frozen=True)
class ModuleEvents:
    """What a module says when nobody asked: it writes its latest event to
    <island_event_dir()>/<key>_dynamic_island.json by atomic swap, and `listener`
    is the command that produces them. One slot, not a queue: the island shows the
    newest important event.
    """

    listener: str = ""
    island_file: Path = field(default_factory=Path)
    # Event name -> priority, set by the module inside the harness's bands
    # (PRIORITY_BANDS).
    priority: dict[str, int] = field(default_factory=dict)


# Where a priority may sit. The harness owns the bands so no module ranks itself above
# everything. A card the user asked for outranks telemetry and loses to hardware that
# needs a human.
PRIORITY_BANDS = {
    "fault":   (90, 100),   # go and look at the machine
    "warning": (70, 89),
    "asked":   (40, 69),    # everything the user asked for
    "routine": (1, 39),     # nice to know
}

#: What a card is worth when it is on screen because the user asked for it.
ASKED_PRIORITY = 50

@dataclass(frozen=True)
class IslandCard:
    """One thing a module can put on screen."""

    name: str
    width: int
    height: int
    # Payload fields this card draws; also what the model is told it is looking at.
    shows: tuple[str, ...] = ()
    prefetch: tuple[str, ...] = ()


@dataclass(frozen=True)
class IslandFace:
    """Everything a module says about the screen."""

    # The payload field that makes two answers the same card (ticker, printer key):
    # decides update-in-place vs replace, and merging.
    about: str
    #: Card entered when this module first takes the screen.
    first: str
    css: str = ""
    cards: tuple[IslandCard, ...] = ()
    # Seconds a stored answer stays worth reusing; 0 means no bound (the default). Not
    # the dwell: this is about how fast the subject's numbers go stale.
    fresh_for: float = 0.0

    def card(self, name: str) -> IslandCard | None:
        for c in self.cards:
            if c.name == name:
                return c
        return None

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(c.name for c in self.cards)


@dataclass(frozen=True)
class ModuleAccount:
    """A web account a module needs. The user signs in once, in their Chrome or the
    eagle's browser; the host hands the module this one site's cookies on stdin
    through `receive`. Nothing from any other site leaves.
    """
    site: str                   # "makerworld.com" -- what the user signs in to
    proof: str                  # a cookie that exists only when signed in
    why: str = ""               # one line for Settings: what it is needed for
    receive: tuple[str, ...] = ()   # module argv that reads the session on stdin


@dataclass(frozen=True)
class ModuleDeck:
    """How a module refines a deck's candidates one at a time: the host calls this
    tool per candidate in the background and merges the answer into that card.
    """
    refine: str         # tool name, unqualified ("download")
    param: str          # the parameter it is called with
    key: str            # the candidate field whose value is passed


@dataclass(frozen=True)
class ModuleManifest:
    key: str
    binary: str
    description: str = ""
    output: str = "text"
    tools: tuple[ModuleTool, ...] = ()
    source: Path | None = field(default=None, compare=False)
    island_config: dict[str, Any] = field(default_factory=dict, compare=False)
    #: The parsed `[island]` section.
    island: IslandFace | None = field(default=None, compare=False)
    events: ModuleEvents | None = None
    deck: ModuleDeck | None = None
    accounts: tuple[ModuleAccount, ...] = ()
    # Python packages from the index: a bundle carries source, not an interpreter.
    requirements: tuple[str, ...] = ()
    # `package.module:callable` run inside the module's environment, which creates the
    # command `binary` points at.
    entrypoint: str = ""
    # What the voice model should know about this module beyond each tool's description.
    # In the system prompt only while it is installed.
    instructions: str = ""
    # Things a person could say to use this module, shown as dashboard suggestions.
    examples: tuple[str, ...] = ()

    @property
    def template(self) -> str:
        """The one card template the host loads for this module."""
        return _file_name(self.island_config.get("template"), "template.html")

    @property
    def stylesheet(self) -> str:
        """`[island] css`, a file name beside the manifest -- never CSS text."""
        return _file_name(self.island_config.get("css"), "style.css")

    def tool(self, name: str) -> ModuleTool | None:
        """By bare name or qualified name — callers have both."""
        for t in self.tools:
            if name in (t.name, t.qualified_name):
                return t
        return None


def _parse_tool(module_key: str, raw: dict[str, Any]) -> ModuleTool:
    name = str(raw.get("name") or "").strip()
    if not name:
        raise ValueError("a [[tools]] entry has no name")

    params: list[ModuleParam] = []
    for pname, praw in (raw.get("params") or {}).items():
        praw = praw or {}
        ptype = str(praw.get("type") or "STRING").upper()
        if ptype not in VALID_TYPES:
            raise ValueError(
                f"{module_key}.{name}.{pname}: unknown type {ptype!r}; "
                f"expected one of {', '.join(sorted(VALID_TYPES))}")
        params.append(ModuleParam(
            name=str(pname),
            type=ptype,
            description=str(praw.get("description") or ""),
            required=bool(praw.get("required")),
            default=praw.get("default"),
        ))

    if any(p.name == CONFIRM_PARAM for p in params):
        raise ValueError(
            f"{module_key}.{name}: {CONFIRM_PARAM!r} is reserved for the "
            f"confirmation handshake and cannot be declared as a parameter")

    # `guarded` (the published standard) and `confirm` (the shipped manifests) are one
    # flag.
    confirm = bool(raw.get("confirm") or raw.get("guarded"))
    internal = bool(raw.get("internal"))
    confirm_prompt = str(raw.get("confirm_prompt") or "")
    if confirm and not confirm_prompt:
        spelling = "guarded" if raw.get("guarded") and not raw.get("confirm") \
            else "confirm"
        raise ValueError(
            f"{module_key}.{name}: {spelling} = true needs a confirm_prompt "
            f"saying what the user is agreeing to")

    danger = str(raw.get("danger") or SAFE).strip().lower()
    if danger not in DANGERS:
        raise ValueError(
            f"{module_key}.{name}: danger must be one of "
            f"{', '.join(DANGERS)}, not {danger!r}")

    seconds = raw.get("seconds")
    if seconds is not None:
        try:
            seconds = float(seconds)
        except (TypeError, ValueError):
            raise ValueError(
                f"{module_key}.{name}: seconds must be a number, "
                f"not {seconds!r}") from None
        if seconds <= 0:
            raise ValueError(f"{module_key}.{name}: seconds must be positive")

    one_at_a_time = raw.get("one_at_a_time")
    if one_at_a_time is not None:
        one_at_a_time = bool(one_at_a_time)

    injects = raw.get("injects")
    injects = tuple(str(i) for i in injects) if isinstance(injects, list) else ()

    def _resources(field: str) -> tuple[str, ...]:
        value = raw.get(field)
        if value is None:
            return ()
        if not isinstance(value, list):
            raise ValueError(
                f"{module_key}.{name}: {field} must be a list of resource "
                f"names, not {value!r}")
        unknown = [str(v) for v in value if str(v) not in RESOURCES]
        if unknown:
            raise ValueError(
                f"{module_key}.{name}: {field} names "
                f"{', '.join(repr(u) for u in unknown)} — the harness knows "
                f"{', '.join(sorted(RESOURCES))}")
        return tuple(str(v) for v in value)

    return ModuleTool(
        module_key=module_key,
        name=name,
        description=str(raw.get("description") or ""),
        argv=tuple(str(a) for a in (raw.get("argv") or ())),
        params=tuple(params),
        confirm=confirm,
        internal=internal,
        confirm_prompt=confirm_prompt,
        seconds=seconds,
        one_at_a_time=one_at_a_time,
        danger=danger,
        injects=injects,
        reads=_resources("reads"),
        writes=_resources("writes"),
        exclusive=bool(raw.get("exclusive")),
    )


def _file_name(value, default: str) -> str:
    """A declared file name as a bare name: never a path out of the manifest's
    directory.
    """
    return PurePath(str(value or default)).name or default


def _names(value) -> tuple[str, ...]:
    """A string or a list of strings, as a tuple without blanks."""
    if isinstance(value, list):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if value and str(value).strip() else ()


def _parse_island(module_key: str, raw: dict[str, Any]) -> IslandFace | None:
    """The [island] section, or None when a module draws nothing. Only a half-
    declared island is an error.
    """
    cards_raw = raw.get("cards")
    if not isinstance(cards_raw, dict) or not cards_raw:
        return None

    cards: list[IslandCard] = []
    for cname, craw in cards_raw.items():
        craw = craw or {}
        size = craw.get("size") if isinstance(craw.get("size"), dict) else {}
        try:
            width = int(size.get("w") or 0)
            height = int(size.get("h") or 0)
        except (TypeError, ValueError):
            raise ValueError(
                f"{module_key}.island.{cname}: size must be "
                f"{{ w = <number>, h = <number> }}") from None
        if width <= 0 or height <= 0:
            raise ValueError(
                f"{module_key}.island.{cname}: needs a size — the pill window "
                f"is sized once at startup for the biggest card installed, so "
                f"an undeclared card would be drawn clipped")

        shows = craw.get("shows")
        cards.append(IslandCard(
            name=str(cname),
            width=width, height=height,
            shows=tuple(str(s) for s in shows) if isinstance(shows, list) else (),
            prefetch=_names(craw.get("prefetch")),
        ))

    fresh_for = raw.get("fresh_for")
    try:
        fresh_for = float(fresh_for) if fresh_for is not None else 0.0
    except (TypeError, ValueError):
        raise ValueError(
            f"{module_key}.island.fresh_for must be a number of seconds, "
            f"not {fresh_for!r}") from None
    if fresh_for < 0:
        raise ValueError(
            f"{module_key}.island.fresh_for cannot be negative")

    return IslandFace(
        about=str(raw.get("about") or "").strip(),
        first=str(raw.get("first") or "").strip(),
        css=str(raw.get("css") or "").strip(),
        cards=tuple(cards),
        fresh_for=fresh_for,
    )


def load_manifest(path: Path | str) -> ModuleManifest:
    """One manifest, or ValueError, so load_manifests can log which file was wrong."""
    path = Path(path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    mod_table = raw.get("module") if isinstance(raw.get("module"), dict) else {}
    key = str(raw.get("key") or mod_table.get("name") or mod_table.get("key") or "").strip()
    if not key:
        raise ValueError(f"{path.name}: no 'key'")
    # The key becomes an install directory that is rmtree'd on replace: ".." or "/etc"
    # would delete the wrong tree. Refused here, where every manifest is read.
    if not _KEY_RE.match(key):
        raise ValueError(
            f"{path.name}: key {key!r} is not a module name. Use letters, "
            f"digits, underscores and hyphens only -- it names a directory, a "
            f"tool the model calls, and a CSS selector.")
    binary = str(raw.get("binary") or mod_table.get("command") or mod_table.get("binary") or "").strip()
    if not binary:
        raise ValueError(f"{path.name}: no 'binary'")

    output = str(raw.get("output") or mod_table.get("output") or "text").lower()
    if output not in ("text", "json"):
        raise ValueError(f"{path.name}: output must be 'text' or 'json', "
                         f"not {output!r}")

    island_config = raw.get("island") if isinstance(raw.get("island"), dict) else {}

    # A module declaring [events] speaks without being asked.
    raw_events = raw.get("events")
    events = None
    if isinstance(raw_events, dict):
        raw_prio = raw_events.get("priority")
        priority: dict[str, int] = {}
        if isinstance(raw_prio, dict):
            for ename, value in raw_prio.items():
                try:
                    priority[str(ename)] = int(value)
                except (TypeError, ValueError):
                    raise ValueError(
                        f"{path.name}: events.priority.{ename} must be a "
                        f"whole number") from None
        events = ModuleEvents(
            listener=str(raw_events.get("listener") or "").strip(),
            island_file=Path(str(raw_events.get("island_file")))
            if raw_events.get("island_file")
            else island_event_dir() / f"{key}_dynamic_island.json",
            priority=priority,
        )
    declared = raw.get("requirements")
    requirements = tuple(str(r) for r in declared) if isinstance(declared, list) else ()
    entrypoint = str(raw.get("entrypoint") or "").strip()
    instructions = str(raw.get("instructions") or "").strip()
    raw_examples = raw.get("examples")
    examples = tuple(str(e).strip() for e in raw_examples
                     if str(e).strip()) if isinstance(raw_examples, list) else ()

    raw_deck = raw.get("deck")
    deck = None
    if isinstance(raw_deck, dict) and raw_deck.get("refine"):
        deck = ModuleDeck(refine=str(raw_deck.get("refine")).strip(),
                          param=str(raw_deck.get("refine_param") or "").strip(),
                          key=str(raw_deck.get("refine_key") or "id").strip())

    accounts = []
    for entry in raw.get("accounts") or ():
        if not isinstance(entry, dict):
            raise ValueError(f"{path.name}: each [[accounts]] must be a table")
        receive = entry.get("receive")
        accounts.append(ModuleAccount(
            site=str(entry.get("site") or "").strip().lower(),
            proof=str(entry.get("proof") or "").strip(),
            why=str(entry.get("why") or "").strip(),
            receive=tuple(str(a) for a in receive) if isinstance(receive, list) else (),
        ))

    manifest = ModuleManifest(
        key=key,
        binary=binary,
        description=str(raw.get("description") or ""),
        output=output,
        tools=tuple(_parse_tool(key, t) for t in (raw.get("tools") or ())),
        source=path,
        island_config=island_config,
        island=_parse_island(key, island_config),
        events=events,
        deck=deck,
        accounts=tuple(accounts),
        requirements=requirements,
        entrypoint=entrypoint,
        instructions=instructions,
        examples=examples,
    )

    # Cross-checks the parser cannot make. Imported here: the validator imports the
    # dataclasses above.
    from .validator import problems           # noqa: PLC0415
    found = problems(manifest)
    if found:
        raise ValueError(f"{path.name}: " + "; ".join(found))
    return manifest


#: Reading one manifest is parsing it; both names are used in the tree.
parse_manifest = load_manifest

def load_manifests(dirs: Iterable[Path | str],
                   on_error=None) -> list[ModuleManifest]:
    """Every valid manifest in `dirs`, sorted by key. A broken one is skipped, never
    fatal.
    """
    found: list[ModuleManifest] = []
    seen: set[str] = set()

    for directory in dirs:
        directory = Path(directory)
        try:
            entries = sorted(set(list(directory.glob("*.toml")) +
                                 list(directory.glob("*/manifest.toml")) +
                                 list(directory.glob("*/*.toml"))))
        except OSError:
            continue                        # unreadable dir is normal
        for path in entries:
            try:
                manifest = load_manifest(path)
            except (ValueError, OSError, tomllib.TOMLDecodeError) as e:
                if on_error is not None:
                    on_error(path, e)
                continue
            # First directory wins, so a user manifest can override a shipped one.
            if manifest.key in seen:
                continue
            seen.add(manifest.key)
            found.append(manifest)

    return sorted(found, key=lambda m: m.key)


def build_argv(manifest: ModuleManifest, tool: ModuleTool,
               args: dict[str, Any]) -> list[str]:
    """The full argv for one call. Never a shell string. An element whose placeholder
    has no value is dropped whole, which is why flags are written
    `--limit={limit}`. Missing required arguments raise before anything is
    spawned.
    """
    args = args or {}
    missing = [p.name for p in tool.params
               if p.required and args.get(p.name) in (None, "")]
    if missing:
        raise ValueError(
            f"{tool.qualified_name} needs {', '.join(missing)}")

    argv = [manifest.binary]
    for element in tool.argv:
        rendered = element
        skip = False
        for param in tool.params:
            token = "{" + param.name + "}"
            if token not in rendered:
                continue
            value = args.get(param.name)
            if value in (None, ""):
                skip = True
                break
            rendered = rendered.replace(token, _render(value))
        if not skip:
            argv.append(rendered)
    return argv


def _render(value: Any) -> str:
    """Scalars as a CLI wants them: booleans as true/false, lists and dicts as JSON
    (str() gives a Python repr).
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
