"""`eagle install <module>`: one command from nothing to a module the eagle uses.

What a module is, from the harness's side: a pip-installable package that ships
a manifest and has a `register` command copying that manifest into the modules
directory. Everything else -- which tools, which cards, which setup questions --
belongs to the module. So installing is the same five steps for every module:

    1. a private environment      ~/.aethelark/venvs/<name>
    2. pip install <source>       from the catalog name, a git URL, or a path
    3. <binary> register          the module installs its own socket
    4. link the binary            ~/.aethelark/bin/<binary>, searched before PATH
    5. fetch a browser            only for a module that depends on Playwright

A running eagle notices the new folder under the modules directory and offers
the new tools on its next turn; nothing needs restarting.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

from .bus import default_manifest_dirs, module_bin_dir

CATALOG_PATH = Path(__file__).resolve().parent / "catalog.toml"
#: Written into each module's environment so `eagle update` knows where it
#: came from without anyone having to remember.
RECORD = ".aethelark-install.json"


def eagle_home() -> Path:
    return Path(os.path.expanduser("~/.aethelark"))


def venvs_dir() -> Path:
    return eagle_home() / "venvs"


def removed_path() -> Path:
    return eagle_home() / "removed-modules.json"


def removed_by_user() -> set[str]:
    """Catalog modules the user took out. The installer's bundle step and a
    re-run of it leave these alone; only an explicit install brings one back."""
    try:
        return set(json.loads(removed_path().read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return set()


def _set_removed(name: str, removed: bool) -> None:
    names = removed_by_user()
    (names.add if removed else names.discard)(name)
    removed_path().parent.mkdir(parents=True, exist_ok=True)
    removed_path().write_text(json.dumps(sorted(names)), encoding="utf-8")


@dataclass
class Source:
    name: str        # directory name under venvs/
    spec: str        # what pip installs
    title: str = ""


def catalog() -> dict[str, dict]:
    try:
        return tomllib.loads(CATALOG_PATH.read_text(encoding="utf-8")).get("modules", {})
    except (OSError, tomllib.TOMLDecodeError):
        return {}


def resolve(spec: str) -> Source:
    """A catalog name, a local path, or a git URL -> what to install."""
    raw = (spec or "").strip()
    if not raw:
        raise ValueError("Say which module to install, for example: eagle install trade")
    lowered = raw.lower()
    for name, entry in catalog().items():
        if lowered == name or lowered in [a.lower() for a in entry.get("aliases", [])]:
            return Source(name=name, spec=str(entry["source"]),
                          title=str(entry.get("title") or name))
    path = Path(os.path.expanduser(raw))
    if path.exists():
        path = path.resolve()
        return Source(name=_slug(path.name), spec=str(path), title=path.name)
    if raw.startswith(("git+", "https://", "http://", "ssh://", "git@")):
        url = raw if raw.startswith("git+") or raw.startswith("git@") else f"git+{raw}"
        tail = raw.rstrip("/").split("/")[-1]
        tail = tail[:-4] if tail.endswith(".git") else tail
        return Source(name=_slug(tail), spec=url, title=tail)
    known = ", ".join(sorted(catalog())) or "none"
    raise ValueError(f"There is no module called {raw!r}. Known modules: {known}. "
                     f"A path or a git URL works too.")


def _slug(text: str) -> str:
    out = "".join(c if c.isalnum() else "-" for c in text.lower()).strip("-")
    return out or "module"


def _print(msg: str) -> None:
    print(msg, flush=True)


_say = _print


def _python_in(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def _bin_in(venv: Path, name: str) -> Path:
    if os.name == "nt":
        for ext in (".exe", ".cmd", ""):
            p = venv / "Scripts" / f"{name}{ext}"
            if p.exists():
                return p
        return venv / "Scripts" / f"{name}.exe"
    return venv / "bin" / name


def _uv() -> str | None:
    return shutil.which("uv") or next(
        (str(p) for p in (Path.home() / ".local/bin/uv", Path.home() / ".local/bin/uv.exe",
                          Path.home() / ".cargo/bin/uv", Path.home() / ".cargo/bin/uv.exe")
         if p.is_file()), None)


def _run(argv: list[str], *, quiet: bool = True, check: bool = True,
         timeout: float | None = None, env: dict | None = None,
         interactive: bool = False) -> subprocess.CompletedProcess:
    if interactive:
        proc = subprocess.run(argv, timeout=timeout, env=env)
    else:
        proc = subprocess.run(argv, capture_output=True, text=True,
                              timeout=timeout, env=env)
    if check and proc.returncode != 0:
        detail = "" if interactive else (proc.stderr or proc.stdout or "").strip()[-1500:]
        raise RuntimeError(f"{Path(argv[0]).name} {' '.join(argv[1:3])} failed "
                           f"({proc.returncode})" + (f":\n{detail}" if detail else ""))
    return proc


def _module_dists(py: Path) -> list[dict]:
    """Packages in this environment installed from a URL or path (PEP 610).

    In a fresh environment that is exactly the module: its dependencies came
    from the index. Their console scripts are the module's commands.
    """
    code = (
        "import importlib.metadata as md, json\n"
        "out=[]\n"
        "for d in md.distributions():\n"
        "    if d.read_text('direct_url.json'):\n"
        "        out.append({'name': d.metadata['Name'], 'scripts': sorted({e.name for e in d.entry_points if e.group=='console_scripts'})})\n"
        "print(json.dumps(out))\n")
    proc = _run([str(py), "-c", code])
    try:
        return json.loads(proc.stdout.strip() or "[]")
    except ValueError:
        return []



def _manifest_binary(modules_dir: Path, key: str) -> str:
    try:
        data = tomllib.loads((modules_dir / key / "manifest.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError):
        return ""
    mod = data.get("module") if isinstance(data.get("module"), dict) else {}
    return str(data.get("binary") or mod.get("command") or mod.get("binary") or "").strip()


def _link(target: Path, link: Path) -> bool:
    """Point `link` at `target`. Never replaces a file that is not ours."""
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink() or not link.exists():
        try:
            if link.is_symlink():
                link.unlink()
            link.symlink_to(target)
            return True
        except OSError:
            pass
    if os.name == "nt":
        shim = link.with_suffix(".cmd")
        shim.write_text(f'@"{target}" %*\r\n', encoding="utf-8")
        return True
    return False


def install(spec: str, *, interactive: bool | None = None,
            progress=None) -> dict:
    """Install one module. Returns what was installed; raises with a sentence.

    `progress`, when given, hears each step as it happens -- the Settings shop
    shows them under the module while it installs in the background.
    """
    def _say(msg: str) -> None:
        _print(msg)
        if progress is not None:
            try:
                progress(msg.strip())
            except Exception:
                pass

    if interactive is None:
        interactive = sys.stdin.isatty()
    source = resolve(spec)
    if source.name in removed_by_user():
        _set_removed(source.name, False)     # asked for by name: it is wanted again
    modules_dir = default_manifest_dirs()[0]
    modules_dir.mkdir(parents=True, exist_ok=True)
    venv = venvs_dir() / source.name
    py = _python_in(venv)

    _say(f"Installing {source.title}…")
    if not py.exists():
        uv = _uv()
        if uv:
            _run([uv, "venv", "--quiet", "--python", sys.executable, str(venv)])
        else:
            _run([sys.executable, "-m", "venv", str(venv)])

    _say("  fetching the module and what it needs (a minute or two)…")
    uv = _uv()
    pip = ([uv, "pip", "install", "--quiet", "--python", str(py)] if uv
           else [str(py), "-m", "pip", "install", "--quiet"])
    _run(pip + [source.spec], timeout=1800)
    dists = _module_dists(py)
    # The module's own code, fetched again even when its version number did
    # not move: "eagle update" from the same git URL must get the new commit.
    for dist in dists:
        refresh = (pip + ["--no-deps", "--reinstall-package", dist["name"], source.spec]
                   if uv else pip + ["--no-deps", "--force-reinstall", source.spec])
        _run(refresh, timeout=1800)
    dists = _module_dists(py)
    scripts = [s for d in dists for s in d.get("scripts", [])]
    if not scripts:
        raise RuntimeError(f"{source.title} installed, but it has no command to "
                           f"run. It is not an eagle module.")

    # `register --json` is the machine half of the module standard: it copies
    # the module's manifest into the modules directory and says where.
    key = registered_with = last_error = ""
    for script in scripts:
        exe = _bin_in(venv, script)
        if not exe.exists():
            continue
        proc = _run([str(exe), "register", "--json"], check=False, timeout=600)
        try:
            answer = json.loads((proc.stdout or "").strip() or "{}")
        except ValueError:
            answer = {}
        if proc.returncode == 0 and answer.get("ok") and answer.get("manifest"):
            key = Path(answer["manifest"]).parent.name
            registered_with = script
            break
        last_error = str(answer.get("error") or proc.stderr or proc.stdout or "")[-600:]
    if not key:
        raise RuntimeError(f"{source.title} installed, but none of its commands "
                           f"({', '.join(scripts)}) could register it with the eagle."
                           + (f"\n{last_error}" if last_error else ""))
    # The human half: a module may have a question for the person installing
    # it (an email the data source requires, say). Only when someone is there.
    if interactive:
        _run([str(_bin_in(venv, registered_with)), "register"], check=False,
             interactive=True, timeout=600)

    binary = _manifest_binary(modules_dir, key) or registered_with
    target = _bin_in(venv, binary)
    linked = []
    if target.exists():
        if _link(target, module_bin_dir() / binary):
            linked.append(str(module_bin_dir() / binary))
        local_bin = Path.home() / ".local" / "bin"
        if local_bin.is_dir() and _link(target, local_bin / binary):
            linked.append(str(local_bin / binary))

    if _has_playwright(py):
        _say("  fetching the browser it uses…")
        _run([str(py), "-m", "playwright", "install", "chromium"], check=False,
             timeout=1800)

    (venv / RECORD).write_text(json.dumps(
        {"source": source.spec, "name": source.name, "key": key,
         "binary": binary, "title": source.title}, indent=2), encoding="utf-8")

    tools = _tool_count(modules_dir, key)
    _say(f"✓ {source.title} is installed: {tools} new things the eagle can do.")
    _say("  If the eagle is running, it picks this up on its own.")
    return {"key": key, "binary": binary, "venv": str(venv), "tools": tools,
            "linked": linked, "title": source.title}



def _has_playwright(py: Path) -> bool:
    proc = _run([str(py), "-c", "import importlib.util,sys;"
                 "sys.exit(0 if importlib.util.find_spec('playwright') else 1)"],
                check=False)
    return proc.returncode == 0


def _tool_count(modules_dir: Path, key: str) -> int:
    try:
        from .manifest import load_manifest
        m = load_manifest(modules_dir / key / "manifest.toml")
        return len([t for t in m.tools if not t.internal])
    except Exception:
        return 0


def shop() -> list[dict]:
    """The catalog as the Settings shop shows it: each module, and whether it
    is installed here.

    Matched by the module's key as well as its catalog name, because a module
    installed by hand or by path is still that module.
    """
    have = {m["key"]: m for m in installed()}
    out = []
    for name, entry in catalog().items():
        names = {name, *[str(a).lower() for a in entry.get("aliases", [])]}
        hit = next((have[k] for k in have if k.lower() in names), None)
        out.append({"name": name, "title": str(entry.get("title") or name),
                    "about": str(entry.get("about") or ""),
                    "default": bool(entry.get("default")),
                    "installed": bool(hit and hit.get("ready")),
                    "key": (hit or {}).get("key", ""),
                    "tools": (hit or {}).get("tools", 0)})
    return out


def installed() -> list[dict]:
    """Every module registered here, and whether its command resolves."""
    from .bus import ModuleBus
    bus = ModuleBus().discover()
    ready = {m.key for m in bus.available()}
    records = {}
    for rec in venvs_dir().glob(f"*/{RECORD}"):
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
            records[data.get("key")] = data
        except (OSError, ValueError):
            continue
    return [{"key": m.key, "title": (records.get(m.key) or {}).get("title") or m.key,
             "about": m.description, "tools": len([t for t in m.tools if not t.internal]),
             "ready": m.key in ready, "source": (records.get(m.key) or {}).get("source", "")}
            for m in bus._manifests]


def remove(key: str) -> list[str]:
    """Unregister a module and delete what `install` created for it."""
    key = (key or "").strip().lower()
    # Any name `install` accepts removes it too: "eagle install trade" then
    # "eagle remove trade" must agree, however the module was installed.
    wanted = {key}
    for name, entry in catalog().items():
        names = {name, *[str(a).lower() for a in entry.get("aliases", [])]}
        if key in names:
            wanted |= names
    modules_dir = default_manifest_dirs()[0]
    removed = []
    record = None
    for rec in venvs_dir().glob(f"*/{RECORD}"):
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if wanted & {str(data.get("key", "")).lower(), str(data.get("name", "")).lower()}:
            record = (rec.parent, data)
            break
    if record is None:
        try:
            key = resolve(key).name if key else key
            candidate = venvs_dir() / key
            if (candidate / RECORD).exists():
                data = json.loads((candidate / RECORD).read_text(encoding="utf-8"))
                record = (candidate, data)
        except ValueError:
            pass
    for name in catalog():
        if name in wanted:
            _set_removed(name, True)
    reg_key = (record[1].get("key") if record else key) or key
    socket = modules_dir / reg_key
    if socket.is_dir():
        shutil.rmtree(socket)
        removed.append(str(socket))
    if record:
        venv, data = record
        binary = data.get("binary") or ""
        for link in (module_bin_dir() / binary, Path.home() / ".local/bin" / binary):
            if binary and link.is_symlink() and str(venv) in os.path.realpath(link):
                link.unlink()
                removed.append(str(link))
        shutil.rmtree(venv, ignore_errors=True)
        removed.append(str(venv))
    return removed


def update(key: str | None = None) -> list[dict]:
    """Reinstall from where each module came from."""
    done = []
    for rec in sorted(venvs_dir().glob(f"*/{RECORD}")):
        try:
            data = json.loads(rec.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if key and key.lower() not in (str(data.get("key", "")).lower(),
                                       str(data.get("name", "")).lower()):
            continue
        done.append(install(data["source"]))
    return done


def bundle(names: list[str], *, install_one=None) -> list[str]:
    """Add the modules that come with the eagle, except any the user removed
    and any already here. Returns the ones it installed."""
    install_one = install_one or install
    have = {m["name"] for m in shop() if m["installed"]}
    skip = removed_by_user() | have
    added = []
    for spec in names:
        try:
            name = resolve(spec).name
        except ValueError:
            continue
        if name in skip:
            continue
        install_one(spec)
        added.append(name)
    return added


USAGE = """Usage:
  eagle install <module>   add a module: trade, 3d, a path, or a git URL
  eagle remove <module>    take one out
  eagle modules            what is installed
  eagle update [module]    reinstall from where it came from"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE)
        return 0
    cmd, rest = argv[0], argv[1:]
    try:
        if cmd == "install":
            if not rest:
                names = ", ".join(sorted(catalog()))
                print(f"Which module? Available: {names}.\n\n{USAGE}")
                return 2
            for spec in rest:
                install(spec)
            return 0
        if cmd == "remove":
            if not rest:
                print(USAGE)
                return 2
            for key in rest:
                gone = remove(key)
                print(f"Removed {key}." if gone else f"There is no module called {key!r} here.")
            return 0
        if cmd == "bundle":
            bundle(rest)
            return 0
        if cmd == "modules":
            mods = installed()
            if not mods:
                names = ", ".join(sorted(catalog()))
                print(f"No modules installed. Add one with: eagle install <name> ({names})")
                return 0
            for m in mods:
                state = "" if m["ready"] else "  (its command is missing -- reinstall it)"
                print(f"{m['title']}  ·  {m['tools']} actions{state}")
            return 0
        if cmd == "update":
            done = update(rest[0] if rest else None)
            if not done:
                print("Nothing to update: no module was installed with `eagle install`.")
            return 0
    except (ValueError, RuntimeError, subprocess.TimeoutExpired) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
