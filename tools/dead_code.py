"""Find code nothing can reach, conservatively enough to act on.

A dead-code finder that is wrong deletes working software, so this one is
built to under-report. Everything it cannot prove is reachable-by-name gets
checked against the ways this codebase actually invokes things without naming
them, and anything matching is kept:

  * decorated handlers - @app.get/@app.post (FastAPI), @pyqtSlot (Qt bridge),
    @pytest.fixture. The framework holds the reference.
  * Qt event overrides - dropEvent, paintEvent, closeEvent and friends are
    called by the event loop, never by us.
  * tool entry points - main.py dispatches by NAME from a string, so a tool
    function can be live while appearing referenced only once.
  * dunder and private-protocol methods.
  * anything named in a string literal anywhere - getattr, dispatch tables,
    config keys.

Run:  .venv/bin/python tools/dead_code.py            # report
      .venv/bin/python tools/dead_code.py --verbose  # with reasons
"""
from __future__ import annotations

import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

#: Decorators that mean "something else calls this".
FRAMEWORK_DECORATORS = (
    "app.get", "app.post", "app.put", "app.delete", "app.websocket",
    "app.on_event", "app.exception_handler", "router.",
    "pyqtSlot", "pyqtProperty", "staticmethod", "classmethod", "property",
    "setter", "getter", "deleter", "fixture", "hookimpl", "atexit.register",
    "cached_property", "abstractmethod", "overload",
)

#: Qt and stdlib protocol methods the runtime calls for us.
PROTOCOL_PREFIXES = ("test_",)
PROTOCOL_SUFFIXES = ("Event", "event")
#: BaseHTTPRequestHandler dispatches by method name — do_GET is called by the
#: stdlib, never by us. Missing these cost a working OAuth callback in the
#: first run of this tool, which is exactly the failure mode a dead-code
#: finder must not have.
HANDLER_PREFIXES = ("do_", "handle_", "log_")

PROTOCOL_NAMES = {
    "run", "main", "setUp", "tearDown", "paintEvent", "closeEvent",
    "mousePressEvent", "mouseMoveEvent", "mouseReleaseEvent", "dropEvent",
    "dragEnterEvent", "resizeEvent", "showEvent", "hideEvent", "keyPressEvent",
    "enterEvent", "leaveEvent", "wheelEvent", "eventFilter", "sizeHint",
    "__getattr__", "__call__",
}


def tracked_python() -> list[Path]:
    out = subprocess.run(["git", "ls-files", "*.py"], cwd=REPO,
                         capture_output=True, text=True).stdout.split()
    return [REPO / f for f in out]


def all_text() -> str:
    """Every tracked text file, concatenated. String mentions count as uses."""
    files = subprocess.run(["git", "ls-files"], cwd=REPO,
                           capture_output=True, text=True).stdout.split()
    chunks = []
    for f in files:
        p = REPO / f
        if p.suffix in (".png", ".jpg", ".ico", ".ttf", ".bundle") or not p.is_file():
            continue
        try:
            chunks.append(p.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            pass
    return "\n".join(chunks)


def _decorator_names(node) -> list[str]:
    out = []
    for d in node.decorator_list:
        out.append(ast.unparse(d) if hasattr(ast, "unparse") else "")
    return out


def _kept_reason(node, name: str) -> str | None:
    if name.startswith("__") or name in PROTOCOL_NAMES:
        return "protocol method"
    if name.startswith(PROTOCOL_PREFIXES) or name.endswith(PROTOCOL_SUFFIXES):
        return "runtime-invoked by convention"
    if name.startswith(HANDLER_PREFIXES):
        return "request-handler dispatch"
    for dec in _decorator_names(node):
        if any(marker in dec for marker in FRAMEWORK_DECORATORS):
            return f"@{dec.split('(')[0]}"
    return None


def find(verbose: bool = False) -> list[tuple[str, int, int, str]]:
    corpus = all_text()
    # Names mentioned inside string literals — dispatch tables, getattr, config.
    quoted = set(re.findall(r"[\"']([A-Za-z_][A-Za-z0-9_]{2,})[\"']", corpus))

    findings = []
    for path in tracked_python():
        rel = str(path.relative_to(REPO))
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            name = node.name
            if _kept_reason(node, name):
                continue
            if name in quoted:
                continue                       # reachable by string
            hits = len(re.findall(rf"\b{re.escape(name)}\b", corpus))
            if hits > 1:
                continue                       # referenced somewhere else
            span = (node.end_lineno or node.lineno) - node.lineno + 1
            findings.append((rel, node.lineno, span, name))
    return sorted(findings, key=lambda f: -f[2])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    findings = find(args.verbose)
    total = sum(f[2] for f in findings)
    print(f"{len(findings)} unreachable functions, {total} lines\n")
    by_file: dict[str, int] = {}
    for rel, line, span, name in findings:
        by_file[rel] = by_file.get(rel, 0) + span
        if args.verbose:
            print(f"  {rel}:{line}  {name}  ({span} lines)")
    if not args.verbose:
        for rel, n in sorted(by_file.items(), key=lambda kv: -kv[1])[:20]:
            print(f"  {n:5} lines  {rel}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
