"""Run model-generated Python without handing it the machine.

`desktop_control(action="task")` asks the brain for code and executes it. The
previous sandbox restricted `__builtins__` to 22 harmless names and then asked
the model, in the prompt, not to delete files or call subprocess. Verified by
running it, all six of these worked:

    read /etc/passwd via the injected Path
    overwrite an arbitrary file
    delete an arbitrary file
    reach subprocess.Popen via ().__class__.__bases__[0].__subclasses__()
    invoke Popen(['id']) and read the real uid back
    reach os through a loaded module's __globals__ and call popen()

A prompt is a request. This is the enforcement.

Two mechanisms, because either alone leaks:

**Containment.** Paths resolve through the same gate `file_controller` uses —
one function, resolution before checking so symlinks cannot step outside, and
already covered by traversal tests. Generated code cannot address anything
outside the allowed roots, and cannot delete at all: the prompt already
promised no deletion, and `file_controller` is the tool that deletes, through
the trash with an undo journal.

**A check before it runs.** Every route to `subprocess` above went through a
private attribute — `__class__`, `__bases__`, `__subclasses__`, `__globals__`.
Generated desktop code has no business touching any name beginning with an
underscore, so the check refuses them outright, and refuses `getattr` reaching
the same names dynamically. That is an allow-list on syntax rather than a
blocklist of known tricks, which is what makes it hold against the next trick.

## What this deliberately does NOT contain

`pyautogui` still drives the real mouse and keyboard, because that is the
tool's entire purpose. Generated code can therefore still type into whatever
window has focus — including a terminal. Containing that means removing the
feature, so it is a stated trade-off, not an oversight: the model can move the
mouse, and cannot read your files, reach the network, or spawn a process.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable

from core.tool_result import ToolResult

#: Attribute and name access that has no legitimate use in generated desktop
#: code, and is the route every demonstrated escape took.
_PRIVATE = "_"

#: Statements that hand execution somewhere this module cannot follow.
_BANNED_NODES = {
    ast.Import: "import statements",
    ast.ImportFrom: "import statements",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
}

#: Callables that re-open the door by name.
_BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input",
                 "globals", "locals", "vars", "breakpoint", "memoryview"}


class Denied(Exception):
    """Generated code asked for something outside the allowed roots."""


def audit_code(code: str) -> str | None:
    """`None` if the code is safe to run, else the reason it is not.

    Checks syntax rather than behaviour, on purpose: it runs before anything
    executes, so there is no state to be tricked about.
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return f"the generated code is not valid Python (syntax error: {e.msg})"

    for node in ast.walk(tree):
        for kind, label in _BANNED_NODES.items():
            if isinstance(node, kind):
                return f"generated code may not use {label}"

        # `x.__class__` — the first step of every escape that worked.
        if isinstance(node, ast.Attribute) and node.attr.startswith(_PRIVATE):
            return (f"generated code may not touch the private attribute "
                    f"'{node.attr}'")

        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name in _BANNED_CALLS:
                return f"generated code may not call {name}()"

        # A private name spelled as a string and fetched later. Static
        # attribute checking is worth nothing without this.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if node.value.startswith("__") and node.value.endswith("__"):
                return (f"generated code may not name the private attribute "
                        f"{node.value!r}")

    return None


def _contain(raw: Any, roots: tuple[Path, ...]) -> Path:
    """Resolve `raw` and prove it sits inside `roots`, or raise `Denied`.

    Deliberately the same shape as `file_controller._resolve`: resolve first,
    then check, so a symlink pointing out is caught by its target rather than
    its name.
    """
    try:
        resolved = Path(str(raw)).expanduser().resolve()
    except (OSError, RuntimeError) as e:      # RuntimeError: symlink loop
        raise Denied(f"{raw} could not be resolved ({e})") from e

    for root in roots:
        try:
            root_resolved = Path(root).resolve()
        except OSError:
            continue
        if resolved == root_resolved or resolved.is_relative_to(root_resolved):
            return resolved

    raise Denied(f"{resolved} is outside the folders this may touch")


class SafePath:
    """A `Path` that cannot leave the allowed roots, and cannot delete.

    Only the operations generated code legitimately needs are exposed. Anything
    absent is absent on purpose — an unknown attribute raises rather than
    falling through to the real `Path`, so this cannot be widened by accident.
    """

    __slots__ = ("_p", "_roots")

    def __init__(self, raw: Any, roots: tuple[Path, ...]) -> None:
        object.__setattr__(self, "_roots", tuple(roots))
        object.__setattr__(self, "_p", _contain(raw, tuple(roots)))

    # -- joining stays contained ------------------------------------------
    def __truediv__(self, other: Any) -> "SafePath":
        return SafePath(self._p / str(other), self._roots)

    def __str__(self) -> str:
        return str(self._p)

    def __repr__(self) -> str:
        return f"Path({str(self._p)!r})"

    def __fspath__(self) -> str:
        return str(self._p)

    def __eq__(self, other: Any) -> bool:
        return str(self) == str(other)

    def __hash__(self) -> int:
        return hash(str(self._p))

    # -- reading -----------------------------------------------------------
    @property
    def name(self) -> str: return self._p.name

    @property
    def stem(self) -> str: return self._p.stem

    @property
    def suffix(self) -> str: return self._p.suffix

    @property
    def parent(self) -> "SafePath": return SafePath(self._p.parent, self._roots)

    def exists(self) -> bool: return self._p.exists()
    def is_dir(self) -> bool: return self._p.is_dir()
    def is_file(self) -> bool: return self._p.is_file()
    def read_text(self, encoding: str = "utf-8") -> str:
        return self._p.read_text(encoding=encoding, errors="replace")

    def read_bytes(self) -> bytes: return self._p.read_bytes()

    def iterdir(self) -> Iterable["SafePath"]:
        return [SafePath(c, self._roots) for c in self._p.iterdir()]

    def glob(self, pattern: str) -> Iterable["SafePath"]:
        return [SafePath(c, self._roots) for c in self._p.glob(pattern)]

    def stat(self): return self._p.stat()

    # -- writing, still contained -----------------------------------------
    def write_text(self, data: str, encoding: str = "utf-8") -> int:
        return self._p.write_text(str(data), encoding=encoding)

    def mkdir(self, parents: bool = False, exist_ok: bool = True) -> None:
        self._p.mkdir(parents=parents, exist_ok=exist_ok)

    # -- deliberately absent: unlink, rmdir, rename, replace, chmod,
    #    symlink_to, touch. Deletion belongs to file_controller, which routes
    #    it through the trash and records an undo.

    #: Why each absent operation is absent. Without this the refusal reads
    #: "'SafePath' object has no attribute 'unlink'", which tells the model
    #: nothing it can act on — it looks like a broken tool rather than a
    #: boundary, and the next thing it tries is a workaround.
    _USE_FILE_CONTROLLER = "Use file_controller, which trashes rather than " \
                           "erases and records an undo."
    _REFUSED = {
        "unlink":      ("deleting files", _USE_FILE_CONTROLLER),
        "rmdir":       ("removing folders", _USE_FILE_CONTROLLER),
        "rmtree":      ("removing folder trees", _USE_FILE_CONTROLLER),
        "rename":      ("renaming or moving", _USE_FILE_CONTROLLER),
        "replace":     ("replacing files", _USE_FILE_CONTROLLER),
        "chmod":       ("changing permissions", ""),
        "symlink_to":  ("creating symlinks", ""),
        "hardlink_to": ("creating hard links", ""),
        "touch":       ("creating empty files", "Use write_text instead."),
        "open":        ("raw file handles", "Use read_text or write_text."),
    }

    def __getattr__(self, name: str):
        entry = SafePath._REFUSED.get(name)
        if entry:
            why, instead = entry
            raise Denied(f"{why} is not available to generated desktop code."
                         + (f" {instead}" if instead else ""))
        raise Denied(f"'{name}' is not available to generated desktop code")


def _safe_shutil(roots: tuple[Path, ...]):
    """`copy2`/`copytree`/`disk_usage`, with both ends contained.

    These take raw strings, so containing `Path` alone would leave a hole
    straight through them — `shutil.copy2('/etc/passwd', ...)` was reachable.
    """
    import shutil as _sh

    def copy2(src, dst):
        return _sh.copy2(_contain(src, roots), _contain(dst, roots))

    def copytree(src, dst, **kw):
        return _sh.copytree(_contain(src, roots), _contain(dst, roots), **kw)

    def disk_usage(p):
        return _sh.disk_usage(_contain(p, roots))

    return type("shutil", (), {"copy2": staticmethod(copy2),
                               "copytree": staticmethod(copytree),
                               "disk_usage": staticmethod(disk_usage)})()


def _safe_getattr(obj, name, *default):
    """`getattr`, minus the ability to spell a private name at runtime."""
    if isinstance(name, str) and name.startswith(_PRIVATE):
        raise Denied(f"'{name}' is not available to generated code")
    return getattr(obj, name, *default)


def build_namespace(roots: tuple[Path, ...], output: list) -> dict:
    """The globals generated code runs against."""
    import time as _time

    def _print(*a):
        output.append(" ".join(str(x) for x in a))

    builtins = {
        "print": _print,
        "len": len, "str": str, "int": int, "float": float,
        "bool": bool, "list": list, "dict": dict, "tuple": tuple, "set": set,
        "range": range, "enumerate": enumerate, "sorted": sorted,
        "isinstance": isinstance, "getattr": _safe_getattr,
        "max": max, "min": min, "sum": sum, "abs": abs, "round": round,
        "zip": zip, "map": map, "filter": filter, "any": any, "all": all,
        "reversed": reversed, "divmod": divmod,
        # Exception types, so generated code can handle its own errors — their
        # absence is why `raise ValueError(...)` used to die as a NameError.
        "Exception": Exception, "ValueError": ValueError, "TypeError": TypeError,
        "OSError": OSError, "KeyError": KeyError, "IndexError": IndexError,
    }

    ns: dict[str, Any] = {
        "__builtins__": builtins,
        "Path": lambda raw: SafePath(raw, roots),
        "time": type("time", (), {"sleep": staticmethod(_time.sleep)})(),
        "shutil": _safe_shutil(roots),
    }

    try:
        import pyautogui                      # noqa: F401
        ns["pyautogui"] = pyautogui           # stated trade-off, see module docstring
    except Exception:
        pass

    return ns


def run_sandboxed(code: str, roots: tuple[Path, ...] | None = None) -> ToolResult:
    """Check, then run. Never raises; never reports work it did not do."""
    roots = tuple(roots or (Path.home(),))

    refusal = audit_code(code)
    if refusal is not None:
        return ToolResult.failure(
            f"Refused to run the generated code: {refusal}.",
            guidance="Nothing ran and nothing changed. Tell the user this step "
                     "was refused; do not describe the task as done.")

    output: list[str] = []
    ns = build_namespace(roots, output)
    try:
        exec(compile(code, "<aethelark_desktop>", "exec"), ns)
    except Denied as e:
        return ToolResult.failure(
            f"Refused: {e}",
            guidance="The generated step asked for something this tool does not "
                     "permit. Nothing ran past that point and nothing was "
                     "deleted. Tell the user what was refused; the message says "
                     "which tool to use instead where there is one.")
    except Exception as e:
        return ToolResult.failure(
            f"Execution error: {e}",
            guidance="The generated step failed. Tell the user it did not work; "
                     "do not claim the desktop changed.")

    return ToolResult.success("\n".join(output) if output else "Done.")
