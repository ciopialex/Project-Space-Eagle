"""Reading, writing and installing a `.aem` — the file a buyer downloads.

A bundle carries everything a module needs to exist on a machine that has never
seen it: the manifest, the island assets, the executable, and the backend code.
Earlier archives carried a six-line shell script pointing at a path on the
author's laptop, so a buyer's download resolved to a directory they do not have.

The format is a ZIP. Windows opens one without a tool, entries can be read
without inflating the archive, and it is what every other distributable bundle
on the planet already is. Gzip tars are still read, because Module_Factory has
been emitting them and those files exist.

The manifest stays TOML. `core/module_bus/manifest.py` parses TOML, AMS-1
documents TOML, and — the part that decides it — the shipped a3d manifest
carries thirty-four lines of comment, including the block explaining why
starting a physical print requires a spoken confirmation. JSON has no comments,
so converting the format would delete the reasoning behind a safety gate. A
manifest that arrives as JSON is read and normalised to TOML on the way in.

Extraction is the security boundary. These files are written to disk and then
executed by the bus, so an entry that climbs out of the module directory, names
an absolute path, or is a symlink is refused rather than sanitised: a bundle
doing any of those is not a bundle with a typo in it.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from dataclasses import dataclass
import os
import shutil
import stat
import tarfile
import tempfile
import tomllib
import zipfile
from pathlib import Path
from typing import Any, Iterable

from .manifest import ModuleManifest, load_manifest


def _signature(bundle) -> bytes | None:
    """Whether this bundle carries a signature, without needing a crypto
    library to answer. Reading a zip entry is not cryptography."""
    import zipfile as _zip
    try:
        with _zip.ZipFile(bundle) as archive:
            if "signature.bin" not in archive.namelist():
                return None
            return archive.read("signature.bin")
    except (_zip.BadZipFile, OSError, KeyError):
        return None


def _verify(bundle, public_key: bytes) -> bool:
    """Verify a signature. Imported here rather than at module scope so that
    reading a manifest — which is most of what this package is for, and what
    the free harness does on every boot — does not require an Ed25519
    implementation to be installed.

    An ImportError propagates on purpose. A missing library must never read as
    a valid signature: that would turn an absent dependency into an open door.
    """
    from .signing import verify_bundle as _verify_bundle

    return _verify_bundle(bundle, public_key)

#: The Aethelark root signing key, public half. A bundle that does not verify
#: against this was not published by us, whatever its filename says. Rotating
#: it means shipping a new harness, which is the point: the trust anchor is not
#: something a downloaded file gets to influence.
ROOT_PUBLIC_KEY = bytes.fromhex(
    "cd6a1e60f054f5db1e56a258ac985e625e82a94f2f9ba3b28738ad4210d90e1f")

#: Never travels: build noise, editor droppings, and caches.
EXCLUDED_PARTS = {"__pycache__", ".git", ".DS_Store", ".pytest_cache",
                  ".mypy_cache", ".ruff_cache", "node_modules"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".swp"}

MANIFEST_NAMES = ("manifest.toml", "manifest.json")


class BundleError(Exception):
    """A bundle that cannot be trusted or cannot be read."""


# ── manifests ───────────────────────────────────────────────────────────────

def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    return json.dumps(str(value))


def _to_toml(data: dict) -> str:
    """Enough TOML to round-trip a manifest. Deliberately small: this exists
    to normalise a JSON manifest into the format the loader reads, not to be a
    general serialiser."""
    lines: list[str] = []
    for key, value in data.items():
        if key == "tools" or isinstance(value, (dict, list)) and key == "tools":
            continue
        if isinstance(value, dict):
            continue
        lines.append(f"{key} = {_toml_scalar(value)}")

    for key, value in data.items():
        if isinstance(value, dict) and key != "tools":
            lines.append("")
            lines.append(f"[{key}]")
            for sub, sub_value in value.items():
                lines.append(f"{sub} = {_toml_scalar(sub_value)}")

    for tool in data.get("tools") or []:
        lines.append("")
        lines.append("[[tools]]")
        params = tool.get("params") or {}
        for key, value in tool.items():
            if key == "params":
                continue
            lines.append(f"{key} = {_toml_scalar(value)}")
        for name, spec in params.items():
            lines.append("")
            lines.append(f"  [tools.params.{name}]")
            for key, value in (spec or {}).items():
                lines.append(f"  {key} = {_toml_scalar(value)}")
    return "\n".join(lines) + "\n"


def read_manifest_bytes(name: str, raw: bytes) -> tuple[dict, str]:
    """A manifest's data and its TOML form, whichever way it arrived."""
    if name.endswith(".json"):
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as e:
            raise BundleError(f"manifest.json is not valid JSON: {e}") from e
        if not isinstance(data, dict):
            raise BundleError("manifest.json is not an object")
        return data, _to_toml(data)

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as e:
        raise BundleError(f"manifest.toml is not valid TOML: {e}") from e
    return data, raw.decode("utf-8")


# ── building ────────────────────────────────────────────────────────────────

def _worth_shipping(relative: Path) -> bool:
    if any(part in EXCLUDED_PARTS for part in relative.parts):
        return False
    if relative.suffix in EXCLUDED_SUFFIXES:
        return False
    return not relative.name.startswith(".")


def _add_tree(archive: zipfile.ZipFile, root: Path, prefix: str = "") -> None:
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root)
        if not _worth_shipping(relative):
            continue
        arcname = f"{prefix}{relative.as_posix()}"
        info = zipfile.ZipInfo(arcname)
        mode = path.stat().st_mode
        # ZIP drops the mode unless it is written into external_attr, and a
        # module whose binary arrives without +x cannot be run.
        info.external_attr = (mode & 0xFFFF) << 16
        info.compress_type = zipfile.ZIP_DEFLATED
        archive.writestr(info, path.read_bytes())


def build_bundle(module_dir: Path | str, output: Path | str,
                 package: Path | str | None = None) -> Path:
    """Pack `module_dir` (and optionally a Python package) into a `.aem`."""
    module_dir = Path(module_dir)
    output = Path(output)

    manifest_path = next((module_dir / name for name in MANIFEST_NAMES
                          if (module_dir / name).is_file()), None)
    if manifest_path is None:
        raise BundleError(
            f"{module_dir} has no manifest.toml — there is nothing to describe "
            f"what this module is or how to call it")
    read_manifest_bytes(manifest_path.name, manifest_path.read_bytes())

    output.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        _add_tree(archive, module_dir)
        if package is not None:
            package = Path(package)
            _add_tree(archive, package, prefix=f"{package.name}/")
    return output


# ── installing ──────────────────────────────────────────────────────────────

def _safe_members(names: Iterable[str], root: Path) -> None:
    """Refuse anything that would land outside `root`."""
    resolved_root = root.resolve()
    for name in names:
        if name.startswith("/") or (len(name) > 1 and name[1] == ":"):
            raise BundleError(f"bundle names an absolute path: {name!r}")
        target = (root / name).resolve()
        if target != resolved_root and resolved_root not in target.parents:
            raise BundleError(
                f"bundle entry would escape the module directory: {name!r}")


def _extract_zip(path: Path, into: Path) -> None:
    with zipfile.ZipFile(path) as archive:
        infos = archive.infolist()
        _safe_members([i.filename for i in infos], into)
        for info in infos:
            if stat.S_ISLNK(info.external_attr >> 16):
                raise BundleError(
                    f"bundle contains a symlink ({info.filename!r}); a link "
                    f"extracted here is a write pointing anywhere")
            target = into / info.filename
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(info))
            mode = (info.external_attr >> 16) & 0o777
            if mode:
                target.chmod(mode)


def _extract_tar(path: Path, into: Path) -> None:
    with tarfile.open(path, "r:*") as archive:
        members = archive.getmembers()
        _safe_members([m.name for m in members], into)
        for member in members:
            if member.issym() or member.islnk():
                raise BundleError(
                    f"bundle contains a link ({member.name!r})")
        # filter="data" is a second line after _safe_members: it strips
        # absolute paths, parent traversal, and special files inside tarfile
        # itself, and is the default from Python 3.14.
        try:
            archive.extractall(into, filter="data")
        except TypeError:                      # Python < 3.12
            archive.extractall(into)



# ── provisioning ────────────────────────────────────────────────────────────

#: `package.module:callable`, and nothing that could be a shell fragment: the
#: value is written into a generated launcher.
ENTRYPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
                        r":[A-Za-z_][A-Za-z0-9_]*\Z")


def vet_entrypoint(raw: str) -> str:
    candidate = str(raw).strip()
    if not ENTRYPOINT.fullmatch(candidate):
        raise BundleError(
            f"refusing entrypoint {raw!r}: expected package.module:callable")
    return candidate


#: A plain PEP 508 requirement and nothing else. The list this is applied to
#: arrives inside a file the user downloaded, and `pip install` runs arbitrary
#: code from whatever index it is aimed at — so an index override, an editable
#: checkout, a VCS URL, a local path or a shell metacharacter is refused before
#: pip is invoked rather than being passed along and hoped about.
_NAME = r"[A-Za-z0-9][A-Za-z0-9._-]*"
_EXTRAS = r"(?:\[[A-Za-z0-9._,-]+\])?"
_OP = r"(?:==|!=|<=|>=|~=|<|>)"
_VERSION = r"[A-Za-z0-9][A-Za-z0-9._*+!-]*"
_SPEC = rf"{_OP}\s*{_VERSION}"
REQUIREMENT = re.compile(
    rf"{_NAME}{_EXTRAS}\s*(?:{_SPEC}\s*(?:,\s*{_SPEC}\s*)*)?\Z")


def vet_requirement(raw: str) -> str:
    """`raw` if it is an ordinary package specifier, else refuse it."""
    candidate = str(raw).strip()
    if not candidate or not REQUIREMENT.fullmatch(candidate):
        raise BundleError(
            f"refusing requirement {raw!r}: only plain package specifiers are "
            f"installed, and this is not one")
    return candidate


@dataclass(frozen=True)
class ProvisionResult:
    """What happened when a module was given its environment."""
    key: str
    ok: bool
    skipped: bool = False
    detail: str = ""


def _run(argv: list[str], cwd: Path) -> tuple[int, str]:
    """Invoke a command as argv. Never through a shell: a requirement holding
    a metacharacter has to be inert even if the vetting above were bypassed."""
    try:
        done = subprocess.run(argv, cwd=str(cwd), capture_output=True,
                              text=True, timeout=1800)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, f"{type(e).__name__}: {e}"
    return done.returncode, (done.stderr or done.stdout or "").strip()


def _site_packages(venv: Path) -> Path | None:
    for candidate in sorted(venv.glob("lib/*/site-packages")):
        return candidate
    windows = venv / "Lib" / "site-packages"
    return windows if windows.is_dir() else None


class ModuleManager:
    """The installed modules on this machine, and how a `.aem` becomes one."""

    def __init__(self, root: Path | str | None = None):
        self.root = Path(root) if root is not None else \
            Path(os.path.expanduser("~/.aethelark/modules"))

    def install_bundle(self, bundle: Path | str,
                       allow_unsigned: bool = False) -> ModuleManifest:
        """Install a `.aem` and return the manifest it declared.

        Extracted to a scratch directory and validated there first: a bundle
        that turns out to be broken must not leave a half-installed module
        behind for the bus to trip over on every subsequent boot.
        """
        bundle = Path(bundle)
        if not bundle.is_file():
            raise BundleError(f"no such bundle: {bundle}")

        # Before extraction, not after: checking a signature once
        # attacker-chosen paths are already on disk is checking after the
        # interesting part has happened.
        signature = _signature(bundle)
        if signature is None:
            if not allow_unsigned:
                raise BundleError(
                    f"{bundle.name} is not signed. Only bundles signed by "
                    f"Aethelark are installed; pass allow_unsigned=True to "
                    f"install one you built yourself.")
        else:
            try:
                trusted = _verify(bundle, ROOT_PUBLIC_KEY)
            except ImportError as e:
                raise BundleError(
                    f"cannot verify {bundle.name}: no Ed25519 implementation "
                    f"is available ({e}). Refusing rather than installing "
                    f"something unverified.") from e
            if not trusted:
                # allow_unsigned is for bundles carrying NO signature. One
                # carrying a signature that does not check out has been
                # tampered with or forged, and no developer flag launders that.
                raise BundleError(
                    f"{bundle.name} carries a signature that does not verify "
                    f"against the Aethelark root key — it has been modified "
                    f"since it was signed, or it was signed by someone else.")

        with tempfile.TemporaryDirectory() as scratch:
            staged = Path(scratch) / "staged"
            staged.mkdir()

            if zipfile.is_zipfile(bundle):
                _extract_zip(bundle, staged)
            elif tarfile.is_tarfile(bundle):
                _extract_tar(bundle, staged)
            else:
                raise BundleError(
                    f"{bundle.name} is neither a zip nor a tar archive")

            manifest_path = next((staged / name for name in MANIFEST_NAMES
                                  if (staged / name).is_file()), None)
            if manifest_path is None:
                raise BundleError(
                    f"{bundle.name} carries no manifest, so nothing can say "
                    f"what module this is")

            _, toml_text = read_manifest_bytes(manifest_path.name,
                                               manifest_path.read_bytes())
            if manifest_path.name.endswith(".json"):
                manifest_path.unlink()
            (staged / "manifest.toml").write_text(toml_text, encoding="utf-8")

            try:
                manifest = load_manifest(staged / "manifest.toml")
            except (ValueError, OSError) as e:
                raise BundleError(f"{bundle.name}: {e}") from e

            destination = self._module_dir(manifest.key)
            self.root.mkdir(parents=True, exist_ok=True)
            # Replaced, not merged: a file dropped between two versions would
            # otherwise survive the upgrade and be discovered forever.
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(staged), str(destination))

        return load_manifest(destination / "manifest.toml")

    def is_ready(self, key: str) -> bool:
        """Whether this module could actually be run right now.

        A directory existing is not the same as a module working: a provision
        that timed out part-way leaves an environment behind with nothing
        runnable in it, which is exactly what was measured against PyPI.
        """
        from .bus import ModuleBus

        manifest_path = self.root / key / "manifest.toml"
        if not manifest_path.is_file():
            return False
        try:
            manifest = load_manifest(manifest_path)
        except (ValueError, OSError):
            return False
        return ModuleBus(manifest_dirs=[self.root])._resolve_binary(manifest) is not None

    def provision(self, key: str, runner=None) -> ProvisionResult:
        """Build this module its own environment and install what it declared.

        Separate from installing because it is the slow, network-bound half:
        a 371 KB bundle pulls 710 MB of dependencies, and a cold run of
        atrade's set timed out against PyPI after 95 seconds. Extraction should
        not be hostage to that, and a download should survive it failing.

        One environment per module rather than a shared one: two modules that
        each work alone stop working together the first time their pins
        disagree.
        """
        module_dir = self.root / key
        manifest_path = module_dir / "manifest.toml"
        if not manifest_path.is_file():
            raise BundleError(f"{key} is not installed")

        manifest = load_manifest(manifest_path)
        # Vetted before anything runs, so a hostile list costs nothing.
        requirements = [vet_requirement(r) for r in manifest.requirements]
        if not requirements:
            return ProvisionResult(key=key, ok=True, skipped=True,
                                   detail="nothing declared")

        run = runner or _run
        venv = module_dir / ".venv"
        code, detail = run([sys.executable, "-m", "venv", str(venv)], module_dir)
        if code != 0:
            return ProvisionResult(key=key, ok=False, detail=detail)

        python = venv / "bin" / "python"
        if not python.exists():
            python = venv / "Scripts" / "python.exe"
        code, detail = run(
            [str(python), "-m", "pip", "install", "--disable-pip-version-check",
             "--no-input", *requirements], module_dir)
        if code != 0:
            return ProvisionResult(key=key, ok=False, detail=detail)

        # The bundle ships source, not a wheel, so the vendored backend has to
        # be put on the environment's path or the module imports nothing.
        site = _site_packages(venv)
        if site is not None:
            site.mkdir(parents=True, exist_ok=True)
            (site / f"aethelark_{key}.pth").write_text(
                f"{module_dir}\n", encoding="utf-8")

        self._write_entrypoint(manifest, module_dir, venv, python)
        return ProvisionResult(key=key, ok=True, detail="provisioned")

    @staticmethod
    def _write_entrypoint(manifest, module_dir: Path, venv: Path,
                          python: Path) -> None:
        """Create the console script the manifest's `binary` points at.

        `pip install <deps>` installs dependencies, not this module, so without
        this the venv has no `bin/<key>` and a buyer's install falls through to
        a PATH lookup for a program they never had — the empty-bundle failure
        again, one layer in. A module shipping its own executable declares no
        entrypoint and gets nothing written over it.
        """
        if not manifest.entrypoint:
            return
        target = vet_entrypoint(manifest.entrypoint)
        module_name, _, callable_name = target.partition(":")

        bin_dir = venv / "bin"
        if not bin_dir.is_dir() and (venv / "Scripts").is_dir():
            bin_dir = venv / "Scripts"
        bin_dir.mkdir(parents=True, exist_ok=True)

        launcher = bin_dir / manifest.key
        launcher.write_text(
            f"#!{python}\n"
            f"import sys\n"
            f"sys.path.insert(0, {str(module_dir)!r})\n"
            f"from {module_name} import {callable_name}\n"
            f"sys.exit({callable_name}())\n",
            encoding="utf-8")
        launcher.chmod(0o755)

    def installed(self) -> list[ModuleManifest]:
        """Every module currently installed under this root."""
        found: list[ModuleManifest] = []
        if not self.root.is_dir():
            return found
        for entry in sorted(self.root.iterdir()):
            manifest = entry / "manifest.toml"
            if manifest.is_file():
                try:
                    found.append(load_manifest(manifest))
                except (ValueError, OSError):
                    continue
        return found

    def _module_dir(self, key: str) -> Path:
        """`self.root / key`, proven to be a child of root.

        Belt to the manifest parser's braces. `load_manifest` refuses a key
        that is not an identifier, so nothing reaching here should be able to
        escape -- but the two callers below both hand the result to
        `shutil.rmtree`, and a recursive delete is not the place to rely on
        validation that happened somewhere else. A key of "..", "/etc" or
        "../.." resolves out of the modules directory under a plain path join,
        and the join has no opinion about it.
        """
        candidate = (self.root / str(key)).resolve()
        root = self.root.resolve()
        if candidate == root or not candidate.is_relative_to(root):
            raise BundleError(
                f"module key {key!r} does not name a directory inside "
                f"{root} -- refusing to touch {candidate}")
        return candidate

    def remove(self, key: str) -> bool:
        """Uninstall a module. True if something was there."""
        target = self._module_dir(key)
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True
