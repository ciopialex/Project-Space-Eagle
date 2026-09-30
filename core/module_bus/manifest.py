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

#: Gemini's declared parameter types. Anything else in a manifest is a typo,
#: and a typo that reached the model as a schema would be rejected at connect
#: time — for the whole session, not just that tool.
VALID_TYPES = {"STRING", "INTEGER", "NUMBER", "BOOLEAN", "ARRAY", "OBJECT"}


@dataclass(frozen=True)
class ModuleParam:
    name: str
    type: str = "STRING"
    description: str = ""
    required: bool = False
    #: What the module does when the argument is left out. A call that passes
    #: it asks for the same thing as one that omits it.
    default: Any = None

    def schema(self) -> dict[str, Any]:
        return {"type": self.type, "description": self.description}


#: What a module key may contain. Deliberately narrow: it has to survive being
#: a directory name, a Gemini function name (`{key}_{tool}`), an HTML attribute
#: value and a filename, and the intersection of those is an identifier.
_KEY_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$", re.IGNORECASE)

#: Parameter name the confirmation handshake travels on. Reserved: a manifest
#: cannot declare it, and it is stripped before argv is built.
from core.confirm import PARAM as CONFIRM_PARAM  # noqa: E402

#: How undoable a tool is, in the same three words `core/capabilities.py`
#: already uses for the eagle's own actions. One vocabulary on both sides of
#: the connector, so a module and the harness cannot describe the same risk
#: differently.
SAFE, UNDOABLE, PERMANENT = "safe", "undoable", "permanent"
DANGERS = (SAFE, UNDOABLE, PERMANENT)

#: What the scheduler knows how to arbitrate over. A module naming something
#: outside this set would declare a hazard nothing checks, which is worse than
#: declaring none — it reads as protected and is not.
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
    #: How long this tool really takes, measured by the module rather than
    #: guessed by the host. `atrade analyze` costs 34-35s against live SEC
    #: filings; the host's 30s default killed it about four seconds before it
    #: would have answered, every time, silently.
    #:
    #: None means undeclared. The harness then picks a conservative default —
    #: undeclared must not mean unlimited, because the tools nobody has thought
    #: about are exactly the ones with no measurement behind them.
    seconds: float | None = None
    #: Unsafe to run twice CONCURRENTLY. Deliberately narrower than the
    #: scheduler's `exclusive`, which stops everything else in the process:
    #: two `a3d download` calls race each other and nothing else, so blocking
    #: the whole tool pool for the duration is a bigger hammer than the hazard.
    #: The module is the only thing that knows which of its own tools collide.
    one_at_a_time: bool | None = None
    #: safe | undoable | permanent. `permanent` requires a confirm_prompt —
    #: enforced in the validator, so the two cannot drift apart.
    danger: str = SAFE
    #: Harness resources this tool looks at and changes: net, web, file, files,
    #: printer, desktop, system, memory, camera, island. The scheduler runs two
    #: tools at once only when neither writes what the other touches, so these
    #: are how a module says "do not run me next to that".
    #:
    #: They were twelve hand-written entries in `main.py`, which is the harness
    #: holding facts only the module knows.
    reads: tuple[str, ...] = ()
    writes: tuple[str, ...] = ()
    #: Nothing else in the whole process may run alongside this. A blunt
    #: instrument, and arguably not a module's to declare — it freezes the
    #: eagle's entire tool pool, not just the resource being fought over.
    #:
    #: Declared here only to reproduce today's behaviour exactly while the
    #: hand-written table is deleted. The narrower replacement is already in
    #: this file: `one_at_a_time` for a tool that races itself, `writes` for a
    #: resource two tools share. Removing this is a separate, reviewable change.
    exclusive: bool = False
    #: Argument names the HOST fills in, not the model. `_selection` carries
    #: what the user has picked on the live card, so a module can act on the
    #: screen without the model transcribing a JSON array back — and so the
    #: confirmation digest covers the screen by construction.
    injects: tuple[str, ...] = ()
    #: True when this tool does something to the physical world or is otherwise
    #: not undoable, and a human must say yes before it runs.
    confirm: bool = False
    #: The consequence, in the words the eagle should SPEAK. `{placeholders}`
    #: are filled from the call's arguments so the question names the real
    #: printer and the real object rather than asking about "something".
    confirm_prompt: str = ""
    #: Invokable, but not offered to the model. For a tool that exists so the
    #: harness can call it — one that takes a payload the harness builds and a
    #: model would have to compose. Every tool declared is a choice the model
    #: has to get right, and the model should not be choosing between a safe
    #: path and the raw one underneath it.
    internal: bool = False

    @property
    def qualified_name(self) -> str:
        """`a3d_search`, not `search`.

        `main.py` holds 35 tool names in one flat namespace and the model picks
        by name. A module whose command is called `search` or `status` — both
        of which `a3d` has — would otherwise collide with a core tool, and the
        loser would be whichever the dict happened to keep.
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
        # Omitted entirely when nothing is required, rather than `[]`. Same
        # instinct as `ToolResult.to_response()` leaving `ok` out: say nothing
        # rather than say something empty and have it read as a claim.
        if self.required:
            parameters["required"] = self.required

        description = self.description
        if self.confirm:
            # The model needs somewhere to put the token, and needs to know the
            # first call is expected to come back with a question rather than a
            # result — otherwise it reports the refusal as a failure.
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
    """Where a module's island events live: this user's alone.

    /tmp is shared by every account on the machine, so any local process could
    write an event file there and put a card on the island. On Linux the
    per-user runtime directory ($XDG_RUNTIME_DIR, mode 0700) is used instead;
    elsewhere the temp directory is already per-user. The host and every
    module must compute this the same way -- the listener inherits the host's
    environment, so they do.
    """
    import os
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if runtime and os.path.isdir(runtime):
        return Path(runtime)
    return Path(tempfile.gettempdir())


@dataclass(frozen=True)
class ModuleEvents:
    """What a module says when nobody asked it anything.

    MODULE_FACTORY_SPEC Pillar 4: a module writes its latest island event to
    <island_event_dir()>/<key>_dynamic_island.json by atomic temp-file swap, and the host reads
    the file. `listener` is the long-lived command that produces them — nothing
    arrives until something runs it.

    One slot, not a queue, and that is deliberate: the island shows one thing
    and decays, so the newest important event is what belongs on screen rather
    than a backlog of stale ones. Modules prioritise before writing.
    """

    listener: str = ""
    island_file: Path = field(default_factory=Path)
    #: event name -> how much it matters. Was a hard-coded table of printer
    #: events in `core/ambient.py`; the module that raises an event is the one
    #: that knows what it costs a human to ignore it. Higher wins. Bands are
    #: the harness's (see PRIORITY_BANDS) — a module orders things inside a
    #: band, it does not get to put itself in one.
    priority: dict[str, int] = field(default_factory=dict)


#: Where a number is allowed to sit, and what that band means. The harness owns
#: these because otherwise every module declares itself the most important
#: thing on the machine. A card is always ASKED: the user asked for it, which
#: outranks routine telemetry and loses to hardware that needs a human.
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
    #: Which payload fields this card draws. Doubles as what the eagle is told
    #: it is looking at, so a field left out of here is invisible to the model
    shows: tuple[str, ...] = ()
    prefetch: tuple[str, ...] = ()


@dataclass(frozen=True)
class IslandFace:
    """Everything a module says about the screen."""

    #: The payload field that makes two answers the SAME card. `ticker` for
    #: atrade, a printer key for a3d. Decides whether a new answer updates the
    #: card in place or replaces it, and whether two payloads merge.
    about: str
    #: Card entered when this module first takes the screen.
    first: str
    css: str = ""
    cards: tuple[IslandCard, ...] = ()
    #: Seconds a stored answer stays worth reusing. 0 means "no bound" and is
    #: the default, because most facts do not rot.
    #:
    #: NOT the dwell. How long a card holds the screen while someone reads it
    #: is the island's own clock, about attention. This is about the
    #: truth of the numbers on it: a share price is worthless a minute later
    #: and a proxy filing is good for months, and neither of those has anything
    #: to do with how long the user looked. The card store keeps answers
    #: between turns and hands them back on a click, so without this it can
    #: serve a price fetched minutes ago as though it just arrived.
    #:
    #: Declared by the module because only the module knows how fast its own
    #: subject changes.
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
    """A web account a module needs, borrowed from the eagle's own browser.

    The user signs in once, in the eagle's browser (Settings -> Modules ->
    Sign in). The module never reads that browser: the host hands it the
    cookies for this one site, on the module's stdin, through `receive`.
    Nothing from any other site leaves the browser.
    """
    site: str                   # "makerworld.com" -- what the user signs in to
    proof: str                  # a cookie that exists only when signed in
    why: str = ""               # one line for Settings: what it is needed for
    receive: tuple[str, ...] = ()   # module argv that reads the session on stdin


@dataclass(frozen=True)
class ModuleDeck:
    """How a module refines the candidates of a deck, one card at a time.

    A tool whose answer carries `candidates` puts a deck on the island. A
    module that can say more about one candidate than its search could --
    by downloading it, to get the real part and its measured size -- names
    that tool here, and the host calls it for each candidate in the
    background and merges the answer into that card. The host learns only
    these three names, never what the module does with them.
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
    #: Python packages this module needs from the index. A bundle carries
    #: source rather than a packed interpreter, so what it depends on has to
    #: be declared somewhere the installer can read it.
    requirements: tuple[str, ...] = ()
    #: `package.module:callable` to run inside the module's own environment.
    #: `pip install <deps>` installs dependencies, not the module, so nothing
    #: would otherwise create the console script the manifest points `binary`
    #: at.
    entrypoint: str = ""
    #: What the voice model should know about this module beyond each tool's
    #: own description: when to reach for it, which tool wins between two
    #: close ones. Added to the system prompt only while the module is
    #: installed, so the harness prompt never names a module it may not have.
    instructions: str = ""
    #: A few things a person could say to use this module, shown as
    #: suggestions in the dashboard. The module knows its own best examples.
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

    # `guarded` is what MODULE_STANDARD.md tells a module author to write;
    # `confirm` is what the shipped manifests already use. They are one flag
    # under two names, and reading only one of them meant a tool authored to
    # the published standard loaded with its gate off — a safety mechanism
    # failing open because its two halves disagreed about its own name.
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
    """A declared file name as a bare name: a manifest names a file beside
    itself, not a path out of its own directory."""
    return PurePath(str(value or default)).name or default


def _names(value) -> tuple[str, ...]:
    """A string or a list of strings, as a tuple without blanks."""
    if isinstance(value, list):
        return tuple(str(v).strip() for v in value if str(v).strip())
    return (str(value).strip(),) if value and str(value).strip() else ()


def _parse_island(module_key: str, raw: dict[str, Any]) -> IslandFace | None:
    """The `[island]` section, or None when a module draws nothing.

    A module with tools and no cards is legal and normal — it contributes
    commands and never takes the screen. Only a HALF-declared island is an
    error, and the validator says which half is missing.
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
    """One manifest, or ValueError. Raises so `load_manifests` can log which
    file was wrong — a manifest that silently became no-tools is worse than a
    loud one, because the module simply stops existing with no explanation."""
    path = Path(path)
    with open(path, "rb") as fh:
        raw = tomllib.load(fh)

    mod_table = raw.get("module") if isinstance(raw.get("module"), dict) else {}
    key = str(raw.get("key") or mod_table.get("name") or mod_table.get("key") or "").strip()
    if not key:
        raise ValueError(f"{path.name}: no 'key'")
    # A key is an IDENTIFIER, and it is used as one in four places: the
    # directory a module is installed into, the `{key}_{tool}` name the model
    # calls, the `[data-module="..."]` selector its card is styled by, and the
    # filename of its manifest. Nothing validated it.
    #
    # The directory is what makes that dangerous. `ModuleManager.install_bundle`
    # computes `self.root / manifest.key` and calls `shutil.rmtree` on it to
    # replace an existing install, and `remove()` does the same. Path join has
    # no opinion about what it is given:
    #
    #     key = "/etc"    ->  rmtree("/etc")
    #     key = ".."      ->  rmtree("~/.aethelark")
    #     key = "../.."   ->  rmtree("~")
    #
    # Install is signature-gated, so this needs a bundle signed by the root key
    # or the --allow-unsigned developer flag -- but a signature proves who made
    # a bundle, not that its manifest is sane, and a typo like "a3d/v2" is a
    # silent wrong install rather than an error. Refused here, once, where
    # every path that reads a manifest goes.
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

    # A module declaring [events] speaks without being asked. atrade has
    # declared one since before 2026-09-03 and this parser dropped it, so the
    # host never learned that either shipped module had anything to say.
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

    # Everything the parser cannot see on its own: files that must exist,
    # names that must point at something, flags that imply each other. Imported
    # here rather than at module scope because the validator imports the
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
    """Every valid manifest in `dirs`, sorted by key.

    A broken manifest is skipped, never fatal. One user hand-editing one TOML
    file must not stop the eagle booting — the failure mode of a discovery
    mechanism has to be "that module is missing", not "nothing starts".
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
            # First directory wins, so a user manifest in ~/.aethelark can
            # override a shipped one rather than duplicating the module.
            if manifest.key in seen:
                continue
            seen.add(manifest.key)
            found.append(manifest)

    return sorted(found, key=lambda m: m.key)


def build_argv(manifest: ModuleManifest, tool: ModuleTool,
               args: dict[str, Any]) -> list[str]:
    """The full argv for one call. Never a shell string.

    Substitution is per element, and an element whose placeholder has no value
    is dropped whole. That is why flags are written `--limit={limit}` as a
    single element: split into `["--limit", "{limit}"]`, an absent value leaves
    a bare `--limit` behind, which then swallows whatever argument follows it.

    Missing required arguments raise before anything is spawned — the model
    gets told what it forgot instead of the module getting a usage error.
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
    """TOML/JSON scalars as a CLI would want them.

    `True` is a Python spelling no CLI knows; booleans reach a module as
    `true`/`false`. Lists and dicts get the same treatment for the same
    reason: `str()` on a list produces a Python repr with single quotes, which
    is not JSON, so a model that passes `jobs` as an array rather than a string
    sent `[{'model_id': '1'}]` down the command line. The module's json.loads
    then failed — after the user had already been asked to approve it, and with
    the repr read back to them as the thing they were approving.
    """
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (list, dict)):
        return json.dumps(value)
    return str(value)
