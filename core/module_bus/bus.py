"""Discovery and invocation. Nothing here may raise into the turn loop.

`ModuleBus.invoke()` is called from the same `ThreadPoolExecutor` that runs the
eagle's own tools, next to the voice loop. Every failure it can have — no
binary, bad arguments, a crash, a hang, output that is not what the manifest
promised — comes back as a `ToolResult` with `ok=False` and a `guidance` line
saying what to do about it. There is no path out of `invoke()` that propagates
an exception.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from core.confirm import Gate
from core.tool_result import ToolResult

from .manifest import (CONFIRM_PARAM, ModuleManifest, ModuleTool,
                       build_argv, load_manifests)

#: How much module output the model is allowed to see. `a3d search` can print a
#: long table and a module is free to be chatty; the context is not.
MAX_OUTPUT_CHARS = 16000

#: Default per-call ceiling. Slicing a model is genuinely slow, so this is
#: generous — but finite, because the thread it holds is shared with the voice
#: loop.
DEFAULT_TIMEOUT_S = 120.0


def module_bin_dir() -> Path:
    """Where `eagle install` links the executables of modules it installed.

    Searched before PATH. The eagle is often started from a desktop icon, and a
    desktop session's PATH is whatever the login shell happened to export --
    ~/.local/bin is on it on some systems and not on others. A module the eagle
    installed itself must not depend on that.
    """
    return Path(os.path.expanduser("~/.aethelark/bin"))


def which_module(name: str) -> str | None:
    """A module's executable: the eagle's own install first, then PATH."""
    base = module_bin_dir()
    for candidate in (base / name, base / f"{name}.exe", base / f"{name}.cmd"):
        try:
            if candidate.is_file() and os.access(candidate, os.X_OK):
                return str(candidate)
        except OSError:
            continue
    return shutil.which(name)


def default_manifest_dirs() -> list[Path]:
    """Where installed modules register themselves.

    One directory, owned by the user: `<module> register` copies a module's
    manifest and island assets into `<dir>/<key>/`. The harness ships no module
    configuration of its own -- a copy it carried used to be synced over the
    module's own on every launch, silently reverting module updates.

    AETHELARK_MODULES_DIR overrides it, which is how tests and a second,
    isolated install point the bus somewhere else.
    """
    override = os.environ.get("AETHELARK_MODULES_DIR")
    return [Path(override).expanduser() if override
            else Path(os.path.expanduser("~/.aethelark/modules"))]


def island_dirs(source: Path | None, key: str) -> list[Path]:
    """Where a module's card files live, relative to its manifest.

    Two layouts. Installed: `<key>/manifest.toml` beside `<key>/island/`.
    Flat: `manifests/<key>.toml` with the cards in a sibling
    `island/<key>/` -- the layout a directory holding several modules'
    manifests uses.
    """
    if source is None:
        return []
    return [source.parent / "island", source.parent.parent / "island" / key]


#: What to say when a module fails without explaining itself.
_UNEXPLAINED = "Read the message — it comes from the module itself."


def _module_failure(stdout: str, stderr: str, returncode: int) -> tuple[str, str]:
    """What a failing module said, preferring its own words to its raw output.

    A module that fails well already writes what a small model needs. a3d, asked
    about a printer nobody configured, returns

        {"error": "There is no printer called 'NOTAPRINTER'.",
         "guidance": "The printers set up here are: CC1, CC2, ..."}

    -- the problem, and the valid options. Flattening that into
    `module tool failed: {"error": ...}` handed the model raw JSON inside a
    sentence, and replaced the one field written to help it choose again with an
    instruction to read what it was already reading.

    The harness runs Gemini 2.5 Flash. Unwrapping this costs nothing and is the
    difference between a model that corrects itself and one that guesses.

    A module with nothing useful to say keeps the old behaviour: its raw output
    is still better than silence.
    """
    for blob in (stdout, stderr):
        if not blob:
            continue
        try:
            parsed = json.loads(blob)
        except ValueError:
            continue
        if isinstance(parsed, dict) and str(parsed.get("error") or "").strip():
            said = str(parsed["error"]).strip()
            hint = str(parsed.get("guidance") or "").strip()
            return said, hint or _UNEXPLAINED
    return (stderr or stdout or f"exit code {returncode}"), _UNEXPLAINED


def _failure_signals(stdout: str) -> dict[str, Any]:
    """The machine-readable part of a module's failure, for the host.

    `error` and `guidance` are for the model and are unwrapped above. A
    `needs_account` names a site the module needs a sign-in for; the host acts
    on it (core/module_bus/accounts.py) instead of leaving the model to
    explain a login it cannot perform.
    """
    try:
        parsed = json.loads(stdout) if stdout else None
    except ValueError:
        return {}
    if not isinstance(parsed, dict):
        return {}
    site = str(parsed.get("needs_account") or "").strip().lower()
    return {"needs_account": site} if site else {}


def model_view(value):
    """The same payload with `_`-prefixed keys removed, at any depth.

    A module sends the island things the model must not read. A browse carries
    five preview meshes at roughly 12.3 KB of base64 each — about 15k tokens of
    quantised triangles the model cannot use, and enough to overrun the message
    ceiling so that what it does receive is truncated mid-blob with the title
    and dimensions cut off the end.

    Marking them with a leading underscore keeps the rule at the point the
    payload is written. The alternative — a manifest field listing which paths
    are UI-only — drifts from the payload it describes, and nothing catches it
    when it does.

    Returns a new structure; the caller's copy is left alone, because the
    island reads that one.
    """
    if isinstance(value, dict):
        return {k: model_view(v) for k, v in value.items()
                if not (isinstance(k, str) and k.startswith("_"))}
    if isinstance(value, list):
        return [model_view(v) for v in value]
    return value


#: What a model writes into a required argument it does not actually know.
_PLACEHOLDERS = frozenset({"unknown", "none", "null", "n/a", "na", "?", "tbd",
                           "not specified", "unspecified"})


class ModuleBus:
    """What domain modules exist on this machine, and how to call them."""

    def __init__(self, manifest_dirs: Sequence[Path | str] | None = None,
                 which: Callable[[str], str | None] = which_module,
                 log: Callable[[str], None] | None = None) -> None:
        self._dirs = list(manifest_dirs) if manifest_dirs is not None \
            else default_manifest_dirs()
        self._which = which
        self._log = log or (lambda msg: None)
        self._manifests: list[ModuleManifest] = []
        self._resolved: dict[str, str] = {}     # key -> absolute binary path
        #: The spoken-yes gate (core/confirm.py), shared in shape with every
        #: other gate in the product. `_confirmations` is its record table.
        self._gate = Gate()
        self._confirmations = self._gate.records

    # ------------------------------------------------------------ discovery

    def discover(self) -> "ModuleBus":
        """Read every manifest and check which binaries actually resolve.

        Cheap and side-effect free: it reads TOML and looks at PATH. Nothing is
        executed, so booting the eagle never starts a module the user did not
        ask for — the same rule `capability/agents.discover_gui_apps` follows.
        """
        self._manifests = load_manifests(
            self._dirs,
            on_error=lambda path, err: self._log(
                f"module manifest {path.name} ignored: {err}"))

        self._resolved = {}
        for manifest in self._manifests:
            try:
                resolved = self._resolve_binary(manifest)
            except Exception:
                resolved = None
            if resolved:
                self._resolved[manifest.key] = str(resolved)
        return self

    def _resolve_binary(self, manifest: ModuleManifest) -> str | None:
        """Where this module's executable actually is, or None.

        A bare name ("atrade") is a PATH lookup, which is how every manifest
        shipped in this repo names its binary.

        A name with a separator ("bin/atrade") is the AMS-1 bundle layout: the
        executable ships *inside* the module directory, beside the manifest
        that declares it, and a `.aem` unpacks to exactly that shape. It is
        resolved against the manifest's own directory and PATH is never
        consulted — falling back to PATH would let a bundle that forgot to
        ship its binary silently run a same-named program that happens to be
        installed on this machine, which is a different program than the one
        the manifest describes.
        """
        declared = manifest.binary
        if not _is_bundled_path(declared):
            return self._which(declared)

        candidate = Path(declared).expanduser()
        if not candidate.is_absolute():
            if manifest.source is None:
                return None             # nothing to resolve relative to
            candidate = manifest.source.parent / candidate

        try:
            candidate = candidate.resolve()
        except OSError:
            return None
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return str(candidate)
        return None

    def available(self) -> list[ModuleManifest]:
        return [m for m in self._manifests if m.key in self._resolved]

    def absent(self) -> list[ModuleManifest]:
        """Declared but not installed.

        Kept and reported rather than dropped. A user who has the Law module's
        repo but never ran `pip install -e .` should be told that, not left
        wondering why the eagle has no opinion about Romanian VAT.
        """
        return [m for m in self._manifests if m.key not in self._resolved]

    # ------------------------------------------------------------ the schema

    def tool_declarations(self) -> list[dict[str, Any]]:
        """Function declarations for every tool of every *available* module.

        Appended to `main.TOOL_DECLARATIONS` at connect time. Only available
        modules contribute: telling the model it can slice an STL on a machine
        with no slicer produces a confident call and a failure, and the model
        has no way to know the difference in advance.

        Internal tools are left out. They are invokable by name — the harness
        calls them — but a tool the model can see is a choice it has to get
        right, and it should not be choosing between a safe path and the raw
        one underneath it.
        """
        return [tool.declaration()
                for manifest in self.available()
                for tool in manifest.tools
                if not tool.internal]

    def instructions(self) -> str:
        """What each installed module wants the voice model to know.

        Only available modules speak here: instructions about a module whose
        binary is missing would send the model to tools it was never given.
        """
        parts = []
        for manifest in self.available():
            if manifest.instructions:
                title = manifest.description or manifest.key
                parts.append(f"MODULE {manifest.key} ({title})\n"
                             f"{manifest.instructions}")
        return "\n\n".join(parts)

    def owns(self, tool_name: str) -> bool:
        """True when `tool_name` is a module tool — available or not.

        Deliberately includes absent modules. Once discovery has seen a
        manifest, the bus answers for that name; otherwise an absent module's
        tool would fall through to the core dispatcher and be reported as an
        unknown tool rather than as an uninstalled module.
        """
        return self._find(tool_name) is not None

    def module_of(self, tool_name: str) -> str | None:
        """The key of the module that owns this tool, or None.

        The host used to take everything before the first underscore of the
        tool's name, which is a different module for any key that has an
        underscore in it.
        """
        found = self._find(tool_name)
        return found[0].key if found else None

    def depth_tools(self, module_key: str) -> tuple[str, ...]:
        """What to fetch, in order, so this module's deeper cards open on data.

        Declared by the module as `prefetch` on its cards. The host used to
        carry the answer for one module, by name, in a table of its own.
        """
        for manifest in self.available():
            if manifest.key != module_key or manifest.island is None:
                continue
            out: list[str] = []
            for card in manifest.island.cards:
                for name in card.prefetch:
                    tool = manifest.tool(name)
                    if tool is not None and tool.qualified_name not in out:
                        out.append(tool.qualified_name)
            return tuple(out)
        return ()

    def live_media(self, module_key: str, subject: str, on: bool) -> str:
        """Start or stop a module's live media for one subject; the URL it
        streams at, or "" when there is none.

        Declared in the module's `[island]`: `live` names the tool and
        `live_param` the argument that receives the subject; the tool also
        receives `state` = on | off and answers with a `url`.
        """
        for manifest in self.available():
            if manifest.key != module_key:
                continue
            raw = manifest.island_config or {}
            tool = manifest.tool(str(raw.get("live") or ""))
            if tool is None:
                return ""
            param = str(raw.get("live_param") or "subject")
            result = self.invoke(tool.qualified_name,
                                 {param: subject, "state": "on" if on else "off"},
                                 timeout_s=tool.seconds or 20.0)
            payload = result.data.get("result") if isinstance(result.data, dict) else None
            if not result.ok or not isinstance(payload, dict):
                return ""
            return str(payload.get("url") or "")
        return ""

    def is_internal(self, tool_name: str) -> bool:
        """A tool the harness calls and the model is never offered."""
        found = self._find(tool_name)
        return bool(found and found[1].internal)

    def island_face(self, tool_name: str):
        """The screen this tool's module declared, or None if it declared none.

        The host asks this to find out whether an answer is something the
        module's own cards can draw, without knowing whose answer it is. A
        module that declares no `[island]` section returns None here, and every
        caller is required to treat that as "carry on as before" rather than as
        "draw nothing" -- declaring no screen is not the same as declaring an
        empty one.
        """
        found = self._find(tool_name)
        return found[0].island if found else None

    def manifest_of(self, tool_name: str) -> ModuleManifest | None:
        found = self._find(tool_name)
        return found[0] if found else None

    def which(self, binary: str) -> str | None:
        """Resolve a module's command the way `invoke` does."""
        return self._which(binary)

    def deck_refiner(self, tool_name: str):
        """(qualified refine tool, ModuleDeck) for this tool's module, or None."""
        found = self._find(tool_name)
        if not found or found[0].deck is None:
            return None
        manifest = found[0]
        tool = manifest.tool(manifest.deck.refine)
        return (tool.qualified_name, manifest.deck) if tool else None

    def island_fields(self, tool_name: str) -> frozenset[str]:
        """Every placeholder this module's card template asks for.

        The companion to `island_face`, for a module that ships a card and
        declares no face. Such a module makes no statement about what its cards
        draw, so `island_face` returns None and the host has nothing to ask —
        and every answer it produces reaches the screen, including the ones its
        template cannot fill a single row of.

        The template is the only declaration a faceless module makes, so the
        host asks it instead. Read here rather than in the caller for the same
        reason `island_face` is: the host learns no module's name to do it. The
        installed island wins over the vendored copy, matching the rule
        everywhere else.

        An empty set means the module ships no template at all, which is not
        the same as a template that draws nothing — the caller is required to
        treat it as "carry on as before", exactly as with a None face.

        The measurement that prompted this, and the payloads it was taken
        from, are in
        tests/test_a_module_that_declares_no_face_still_earns_the_screen.py.
        """
        found = self._find(tool_name)
        if not found:
            return frozenset()
        manifest = found[0]
        candidates = [d / manifest.template
                      for d in island_dirs(manifest.source, manifest.key)]
        for path in candidates:
            try:
                if path.is_file():
                    text = path.read_text(encoding="utf-8")
                    return frozenset(re.findall(r"\{([a-z0-9_]+)\}", text, re.I))
            except OSError:
                continue
        return frozenset()

    def _find(self, tool_name: str):
        for manifest in self._manifests:
            tool = manifest.tool(tool_name)
            if tool is not None and tool.qualified_name == tool_name:
                return manifest, tool
        return None

    # ---------------------------------------------------------- invocation

    def invoke(self, tool_name: str, args: dict[str, Any] | None = None,
               timeout_s: float = DEFAULT_TIMEOUT_S) -> ToolResult:
        """Run one module tool. Returns; never raises."""
        found = self._find(tool_name)
        if found is None:
            known = ", ".join(sorted(
                t.qualified_name for m in self._manifests for t in m.tools))
            return ToolResult.failure(
                f"No module tool called {tool_name!r}.",
                guidance=f"Known module tools: {known or 'none'}.")

        manifest, tool = found

        binary = self._resolved.get(manifest.key)
        if binary is None:
            # A bundle declares its executable by path, so "put it on PATH" is
            # advice for a fix that does not apply: the file is meant to be
            # inside the module directory, and the real fault is a bundle that
            # arrived incomplete.
            if manifest.binary.startswith(".venv") or "/.venv/" in manifest.binary:
                # The module is installed; it just has no environment yet.
                # Saying "re-install" would send the user back to a download
                # they already have.
                guidance = (f"The {manifest.key} module is installed but its "
                            f"setup did not finish, so what it needs is not "
                            f"there yet. Tell the user to install it again the "
                            f"same way (eagle --install-module with the file); "
                            f"that retries the setup.")
            elif _is_bundled_path(manifest.binary):
                guidance = (f"Its manifest expects the executable at "
                            f"'{manifest.binary}' inside the module folder, and "
                            f"nothing runnable is there. Re-install the module.")
            else:
                guidance = (f"Install its CLI ('{manifest.binary}') and make sure "
                            f"it is on PATH, then ask again.")
            return ToolResult.failure(
                f"The {manifest.key} module is not installed on this machine, "
                f"so {tool_name} did nothing.",
                guidance=guidance,
                module=manifest.key, installed=False)

        args = dict(args or {})
        had_token = CONFIRM_PARAM in args
        token = str(args.pop(CONFIRM_PARAM, "") or "")

        try:
            argv = build_argv(manifest, tool, args)
        except ValueError as e:
            # Checked BEFORE the gate on purpose: never ask a human to approve
            # a call that could not have run anyway.
            return ToolResult.failure(
                str(e),
                guidance="Call it again with that argument filled in.",
                module=manifest.key)

        unknown = [p for p in tool.params
                   if p.required and str(args.get(p.name, "")).strip().lower() in _PLACEHOLDERS]
        if unknown:
            p = unknown[0]
            return ToolResult.failure(
                f"{tool_name} needs to know: {p.description or p.name}",
                guidance=(f"Nothing was done and nothing was asked. Ask the user "
                          f"for the {p.name.replace('_', ' ')}, then call it again "
                          f"with their answer."),
                module=manifest.key)

        if tool.confirm:
            gate = self._check_confirmation(tool, args, token, had_token=had_token)
            if gate is not None:
                return gate

        argv[0] = binary                    # the resolved path, not the name
        started = time.monotonic()

        try:
            proc = subprocess.Popen(
                argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
            try:
                stdout_str, stderr_str = proc.communicate(timeout=timeout_s)
            except subprocess.TimeoutExpired:
                try:
                    import signal
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except Exception:
                    pass
                proc.communicate()
                return ToolResult.failure(
                    f"The {manifest.key} module timed out after {timeout_s:.0f}s "
                    f"and was stopped.",
                    guidance="Try a narrower request, or run it directly to see "
                             "where it is stuck.",
                    module=manifest.key, timed_out=True)
            proc_returncode = proc.returncode
        except (OSError, ValueError) as e:
            return ToolResult.failure(
                f"Could not run the {manifest.key} module: {e}",
                guidance=f"Check that '{manifest.binary}' still works when you "
                         f"run it yourself.",
                module=manifest.key)

        stdout = (stdout_str or "").strip()
        stderr = (stderr_str or "").strip()
        print(f"[module] {_shown_argv(argv, tool)} -> exit {proc_returncode} "
              f"({(time.monotonic() - started) * 1000:.0f}ms)")
        if stderr and proc_returncode != 0:
            for line in stderr.splitlines()[-4:]:
                print(f"[module]   stderr: {line[:200]}")

        if proc_returncode != 0:
            detail, hint = _module_failure(stdout, stderr, proc_returncode)
            return ToolResult.failure(
                f"{manifest.key} {tool.name} failed: {_clip(detail)}",
                guidance=hint,
                module=manifest.key, exit_code=proc_returncode,
                **_failure_signals(stdout))

        if manifest.output == "json":
            try:
                parsed = json.loads(stdout) if stdout else None
            except ValueError as e:
                # The manifest promised JSON. Handing the model a Rich table
                # under a `result` key would be the invented structure this
                # whole design refuses.
                return ToolResult.failure(
                    f"{manifest.key} {tool.name} declares JSON output but did "
                    f"not emit valid JSON ({e}).",
                    guidance=f"Its manifest says output = \"json\"; either the "
                             f"module changed or the manifest is wrong.",
                    module=manifest.key, raw=_clip(stdout, 800))
            # What the model hears is the payload minus its UI-only fields;
            # what the island gets is `result`, whole. Re-serialised rather
            # than passing raw stdout through, because the two now differ.
            # ensure_ascii=False because the default escapes every non-ASCII
            # character to \uXXXX: alaw answers Romanian tax questions and its
            # payloads inflate by a third or more, which can push a payload
            # that fitted the ceiling past it and truncate the model's copy.
            spoken = (json.dumps(model_view(parsed), ensure_ascii=False)
                      if parsed is not None else stdout)
            self._warn_if_clipped(tool, spoken)
            return ToolResult.success(
                _clip(spoken) or f"{manifest.key} {tool.name} returned nothing.",
                module=manifest.key, result=parsed, exit_code=proc_returncode)

        self._warn_if_clipped(tool, stdout)
        return ToolResult.success(
            _clip(stdout) or f"{manifest.key} {tool.name} returned nothing.",
            module=manifest.key, exit_code=proc_returncode)

    @staticmethod
    def _warn_if_clipped(tool: ModuleTool, text: str) -> None:
        """Say out loud when a module's answer did not fit.

        Overflow used to be silent. `_clip` cut the model's copy mid-structure,
        appended a character count, and nobody found out until a person got a
        confidently incomplete answer — measured on a real insider ledger:
        47,586 characters against this ceiling, 121 of 182 filings dropped, and
        the model answered from the 61 it could see without hedging.

        The module author is the only one who can fix that, and they are not
        reading the harness's source. This line is how they find out. It costs
        one comparison on a path that has already serialised the payload.
        """
        if len(text) <= MAX_OUTPUT_CHARS:
            return
        print(f"[bus] {tool.qualified_name}: {len(text):,} chars over the "
              f"{MAX_OUTPUT_CHARS:,} ceiling — {len(text) - MAX_OUTPUT_CHARS:,} "
              f"dropped before the model saw them. The module must send less: "
              f"summarise, rank, or mark bulk data with a leading underscore "
              f"(docs/MODULE_CONTRACT.md).")

    # --------------------------------------------------------- confirmation

    @staticmethod
    def _as_asked(tool: ModuleTool, args: dict[str, Any]) -> dict[str, Any]:
        """The request with empty arguments and stated defaults left out, so a
        yes survives the model spelling out what it had left implicit."""
        defaults = {p.name: p.default for p in tool.params if p.default is not None}

        def same(a, b) -> bool:
            return str(a).strip().lower() == str(b).strip().lower()

        return {k: v for k, v in args.items()
                if v not in (None, "") and not (k in defaults and same(v, defaults[k]))}

    def _check_confirmation(self, tool: ModuleTool, args: dict[str, Any],
                            token: str, had_token: bool = False) -> ToolResult | None:
        """None to proceed; a ToolResult to stop and ask.

        The token is the mechanism that stops the model answering its own
        question. It cannot invent one, so a confirmed call always has a first
        call behind it — and that first call is what the user heard.
        """
        args = self._as_asked(tool, args)
        cleared, refused, lead = self._gate.check(
            tool.qualified_name, args, token, had_token=had_token)
        if cleared:
            return None

        # A refused token still gets the real question, in the same answer.
        # Measured live on 2026-09-23: the voice model filled confirm_token on
        # its FIRST call to a gated tool. Refusing without asking made the user
        # say yes twice -- once to the model's own question, once to the one a
        # second call finally fetched. Nothing runs either way; this only
        # saves the round trip.
        new_token = self._gate.issue(tool.qualified_name, args)

        # Fill what the call DID supply, whatever it left out.
        #
        # This was `.format(**args)` with the raw prompt as the fallback, so a
        # single missing key discarded every substitution -- including the ones
        # that would have worked. `a3d_print`'s prompt names {query_or_id} and
        # {printer}; `printer` is optional, so "print the benchy" reached the
        # user as
        #
        #     This will start a real print of '{query_or_id}' on printer {printer}.
        #
        # Template variables, read out loud, in the confirmation for the one
        # action this codebase calls irreversible: a print burns filament and
        # hours and cannot be stopped remotely. The `Requested:` line beneath
        # it had the real value the whole time.
        #
        # `format_map` with a __missing__ substitutes per key, so an omitted
        # optional costs that phrase and nothing else. A module that omits a
        # REQUIRED argument never gets here -- build_argv above rejects it
        # first, deliberately, so nobody is asked to approve a call that could
        # not have run.
        class _Filled(dict):
            def __missing__(self, key: str) -> str:
                # "on the default printer", not "on the default": the model
                # says this sentence to a person, word for word.
                return "the default " + key.replace("_", " ")

        try:
            consequence = tool.confirm_prompt.format_map(_Filled(args))
        except (IndexError, ValueError):
            # Positional or malformed braces in a hand-written prompt. The
            # question still has to be askable.
            consequence = tool.confirm_prompt

        detail = ", ".join(f"{k}={v}" for k, v in sorted(args.items()) if v != "")
        requested = f" Requested: {detail}." if detail else ""
        return ToolResult.failure(
            f"{consequence}{requested} Shall I go ahead?",
            guidance=((f"{refused} " if refused else "")
                      + (f"{lead} " if lead else "")
                      + "Nothing has started. Ask the user this question OUT "
                      "LOUD and wait for an answer. If they say yes, call this "
                      "tool again with the same arguments plus "
                      f"confirm_token={new_token!r}. If they say no, do not "
                      "call it again."),
            module=tool.module_key, needs_confirmation=True,
            confirm_token=new_token)

    # ------------------------------------------------------------ reporting

    def summary(self) -> str:
        """One line per declared module. Read by the doctor and the HUD."""
        if not self._manifests:
            return "No domain modules declared."
        lines = []
        for manifest in self._manifests:
            binary = self._resolved.get(manifest.key)
            if binary:
                lines.append(f"{manifest.key}: {len(manifest.tools)} tool(s) "
                             f"via {binary}")
            else:
                lines.append(f"{manifest.key}: not installed "
                             f"('{manifest.binary}' not on PATH)")
        return "\n".join(lines)


def _is_bundled_path(declared: str) -> bool:
    """True when a manifest names its executable by path rather than by name.

    "atrade" is a PATH lookup. "bin/atrade" is the AMS-1 bundle layout, where
    the executable ships inside the module folder beside its manifest.
    """
    separators = {os.sep, os.altsep} - {None}
    return any(sep in declared for sep in separators)


def _clip(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    text = text or ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n… [{len(text) - limit} more characters truncated]"


#: Process-wide bus. Discovery is cheap but not free (a glob and a few PATH
#: lookups), and the answer only changes when the user installs something.
_BUS: ModuleBus | None = None


def get_bus(refresh: bool = False) -> ModuleBus:
    global _BUS
    if _BUS is None or refresh:
        _BUS = ModuleBus().discover()
    return _BUS


_SECRET_WORDS = ("code", "token", "password", "secret", "key", "cookie")


def _shown_argv(argv: list[str], tool) -> str:
    secret = [p.name for p in tool.params if any(w in p.name.lower() for w in _SECRET_WORDS)]
    shown = [os.path.basename(argv[0])]
    for part in argv[1:]:
        if any(part.startswith(f"--{n.replace('_', '-')}=") or part.startswith(f"--{n}=")
               or part.startswith("--code=") for n in secret):
            part = part.split("=", 1)[0] + "=***"
        shown.append(part)
    return " ".join(shown)[:300]
