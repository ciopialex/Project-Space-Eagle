"""Run model-generated Python (desktop_control's `task`) without handing it the
machine.

Containment: paths resolve through the same kind of gate file_controller uses
(resolve, then check), and nothing can be deleted.

A check before it runs: every known escape went through a private attribute
(__class__, __bases__, __subclasses__, __globals__), so any name starting with an
underscore is refused, including via getattr and str.format. An allow-list on
syntax, not a blocklist of tricks.

Not contained: pyautogui still drives the real mouse and keyboard, which is the
tool's purpose, so generated code can type into whatever window has focus. It
cannot read files outside the roots, reach the network, or spawn a process.
"""
from __future__ import annotations

import ast
from pathlib import Path
from typing import Any, Iterable

from core.tool_result import ToolResult

# Attribute and name access with no legitimate use here: the route of every demonstrated
# escape.
_PRIVATE = "_"

# Statements that hand execution somewhere this module cannot follow.
_BANNED_NODES = {
    ast.Import: "import statements",
    ast.ImportFrom: "import statements",
    ast.Global: "global",
    ast.Nonlocal: "nonlocal",
}

# Callables that re-open the door by name.
_BANNED_CALLS = {"eval", "exec", "compile", "__import__", "open", "input",
                 "globals", "locals", "vars", "breakpoint", "memoryview"}


class Denied(Exception):
    """Generated code asked for something outside the allowed roots."""


def audit_code(code: str) -> str | None:
    """None if the code is safe to run, else why not. Checks syntax before anything
    executes.
    """
    try:
        tree = ast.parse(code or "")
    except SyntaxError as e:
        return f"the generated code is not valid Python (syntax error: {e.msg})"

    for node in ast.walk(tree):
        for kind, label in _BANNED_NODES.items():
            if isinstance(node, kind):
                return f"generated code may not use {label}"

        # `x.__class__`.
        if isinstance(node, ast.Attribute) and node.attr.startswith(_PRIVATE):
            return (f"generated code may not touch the private attribute "
                    f"'{node.attr}'")

        if isinstance(node, ast.Call):
            name = getattr(node.func, "id", None)
            if name in _BANNED_CALLS:
                return f"generated code may not call {name}()"

        # A private name anywhere in a string: covers getattr with a split name ('__cla'
        # + 'ss__') and '{0.__class__}'.format(()), which resolves attributes with no
        # getattr call.
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "__" in node.value:
                return (f"generated code may not put a private name in a "
                        f"string ({node.value[:40]!r})")

        # `.format` reaches attributes without an attribute node; f-strings do the same
        # job and are checked.
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr in {"format", "format_map"}:
                return ("generated code may not call .format() — use an "
                        "f-string, whose attribute access can be checked")

    return None


def _contain(raw: Any, roots: tuple[Path, ...]) -> Path:
    """Resolve `raw` and prove it is inside `roots`, or raise Denied. Resolve first,
    so a symlink is judged by its target.
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
    """A Path that cannot leave the allowed roots and cannot delete. Unknown
    attributes raise rather than fall through.
    """

    __slots__ = ("_p", "_roots")

    def __init__(self, raw: Any, roots: tuple[Path, ...]) -> None:
        object.__setattr__(self, "_roots", tuple(roots))
        object.__setattr__(self, "_p", _contain(raw, tuple(roots)))

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

    def write_text(self, data: str, encoding: str = "utf-8") -> int:
        return self._p.write_text(str(data), encoding=encoding)

    def mkdir(self, parents: bool = False, exist_ok: bool = True) -> None:
        self._p.mkdir(parents=parents, exist_ok=exist_ok)

    # Absent: unlink, rmdir, rename, replace, chmod, symlink_to, touch. Deletion belongs
    # to file_controller (trash plus undo).

    # Why each absent operation is absent, so the refusal reads as a boundary.
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
    """copy2 / copytree / disk_usage with both ends contained: they take raw strings."""
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


# The mouse, keyboard and screen functions allowed. Never the module:
# pyautogui.sys.modules['os'] reaches a shell.
_INPUT_FUNCTIONS = (
    "click", "doubleClick", "tripleClick", "rightClick", "middleClick",
    "moveTo", "moveRel", "move", "dragTo", "dragRel", "drag",
    "mouseDown", "mouseUp", "scroll", "hscroll", "vscroll",
    "press", "keyDown", "keyUp", "hotkey", "write", "typewrite",
    "position", "size", "onScreen", "screenshot", "locateOnScreen",
    "locateCenterOnScreen", "pixel", "pixelMatchesColor", "sleep",
)


def _input_only(module):
    funcs = {n: staticmethod(getattr(module, n)) for n in _INPUT_FUNCTIONS
             if callable(getattr(module, n, None))}
    return type("pyautogui", (), funcs)()


def _safe_getattr(obj, name, *default):
    """getattr, minus private names spelled at run time."""
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
        ns["pyautogui"] = _input_only(pyautogui)   # stated trade-off, see module docstring
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
