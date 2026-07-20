"""Condensed repository maps for agent context packing (Challenge F).

Gives sub-agents ~90% of a codebase's architectural context in ~10% of
the tokens: class/function signatures for Python (stdlib ast — exact),
declaration lines for JS/TS, and a bare file inventory for the rest.
No native parser dependencies.
"""

import ast
import re
from pathlib import Path

SKIP_DIRS = {".git", "node_modules", ".venv", "venv", "__pycache__",
             ".space_eagle", "dist", "build", ".next", "target"}
CODE_EXTS = {".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css",
             ".json", ".md", ".rs", ".go", ".java", ".rb", ".sh"}
MAX_FILE_BYTES = 400_000

_JS_DECL = re.compile(
    r"^\s*(?:export\s+)?(?:default\s+)?"
    r"(?:async\s+)?(?:function\s+\w+\s*\([^)]*\)|class\s+\w+"
    r"|(?:const|let|var)\s+\w+\s*=\s*(?:async\s*)?(?:\([^)]*\)|\w+)\s*=>)",
    re.MULTILINE)


def _py_signatures(text: str) -> list[str]:
    out = []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return ["(unparseable)"]
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = ", ".join(a.arg for a in node.args.args)
            out.append(f"def {node.name}({args})")
        elif isinstance(node, ast.ClassDef):
            methods = [n.name for n in node.body
                       if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
            shown = ", ".join(methods[:8]) + ("…" if len(methods) > 8 else "")
            out.append(f"class {node.name}: {shown}")
    return out


def _js_signatures(text: str) -> list[str]:
    return [" ".join(m.group(0).split())[:90]
            for m in _JS_DECL.finditer(text)][:20]


def _rank(rel: Path) -> tuple:
    """Entry points and shallow files first."""
    name = rel.stem.lower()
    prominent = 0 if name in ("main", "app", "index", "server", "cli",
                              "setup", "core") else 1
    return (len(rel.parts), prominent, str(rel))


def build_repo_map(root, max_chars: int = 6000, max_files: int = 150) -> str:
    root = Path(root).resolve()
    files = []
    stack = [root]
    while stack and len(files) < max_files * 3:
        d = stack.pop()
        try:
            for entry in sorted(d.iterdir()):
                if entry.is_dir():
                    if entry.name not in SKIP_DIRS and not entry.name.startswith("."):
                        stack.append(entry)
                elif entry.suffix in CODE_EXTS:
                    files.append(entry)
        except OSError:
            continue

    files.sort(key=lambda f: _rank(f.relative_to(root)))
    lines = []
    for f in files[:max_files]:
        rel = f.relative_to(root)
        try:
            if f.stat().st_size > MAX_FILE_BYTES:
                lines.append(f"{rel} (large file)")
                continue
            if f.suffix == ".py":
                sigs = _py_signatures(f.read_text(encoding="utf-8", errors="replace"))
            elif f.suffix in (".js", ".jsx", ".ts", ".tsx"):
                sigs = _js_signatures(f.read_text(encoding="utf-8", errors="replace"))
            else:
                sigs = []
        except OSError:
            continue
        lines.append(f"{rel}:")
        lines.extend(f"  {s}" for s in sigs[:15])
        if not sigs:
            lines[-1] = f"{rel}"
        if sum(len(l) + 1 for l in lines) > max_chars:
            lines.append("… (map truncated)")
            break
    return "\n".join(lines) or "(empty project)"
