"""Reading, writing and installing a .aem, the file a buyer downloads: manifest,
island assets, executable and backend code.

A ZIP (gzip tars are still read). The manifest stays TOML, which keeps its
comments, including the reasons behind safety gates; a JSON manifest is normalised
to TOML on the way in.

Extraction is the security boundary: an entry that climbs out of the module
directory, names an absolute path, or is a symlink is refused, not sanitised.
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
    """Whether this bundle carries a signature. Reading a zip entry needs no crypto
    library.
    """
    import zipfile as _zip
    try:
        with _zip.ZipFile(bundle) as archive:
            if "signature.bin" not in archive.namelist():
                return None
            return archive.read("signature.bin")
    except (_zip.BadZipFile, OSError, KeyError):
        return None


def _verify(bundle, public_key: bytes) -> bool:
    """Verify a signature. Imported here so reading manifests needs no Ed25519
    library. An ImportError propagates: a missing library must never read as
    valid.
    """
    from .signing import verify_bundle as _verify_bundle

    return _verify_bundle(bundle, public_key)

# The Aethelark root signing key, public half. Rotating it means shipping a new harness.
ROOT_PUBLIC_KEY = bytes.fromhex(
    "cd6a1e60f054f5db1e56a258ac985e625e82a94f2f9ba3b28738ad4210d90e1f")

# Never packed: build noise, editor files, caches.
EXCLUDED_PARTS = {"__pycache__", ".git", ".DS_Store", ".pytest_cache",
                  ".mypy_cache", ".ruff_cache", "node_modules"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo", ".swp"}

MANIFEST_NAMES = ("manifest.toml", "manifest.json")


class BundleError(Exception):
    """A bundle that cannot be trusted or cannot be read."""


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    return json.dumps(str(value))


def _to_toml(data: dict) -> str:
    """Enough TOML to round-trip a manifest. Only for normalising a JSON manifest."""
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
        # ZIP drops the mode unless written into external_attr, and a binary needs +x.
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
        # filter="data" is a second line after _safe_members (the default from Python
        # 3.14).
        try:
            archive.extractall(into, filter="data")
        except TypeError:                      # Python < 3.12
            archive.extractall(into)


# `package.module:callable` only: it is written into a generated launcher.
ENTRYPOINT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
                        r":[A-Za-z_][A-Za-z0-9_]*\Z")


def vet_entrypoint(raw: str) -> str:
    candidate = str(raw).strip()
    if not ENTRYPOINT.fullmatch(candidate):
        raise BundleError(
            f"refusing entrypoint {raw!r}: expected package.module:callable")
    return candidate


# A plain PEP 508 requirement and nothing else: the list arrives in a downloaded file
# and pip runs code from its index, so index overrides, editable checkouts, VCS URLs,
# local paths and shell metacharacters are refused before pip runs.
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
    """Run a command as argv, never through a shell."""
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
        """Install a .aem and return its manifest. Extracted and validated in a
        scratch directory first, so a broken bundle leaves nothing half-installed.
        """
        bundle = Path(bundle)
        if not bundle.is_file():
            raise BundleError(f"no such bundle: {bundle}")

        # Check the signature before extraction.
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
                # allow_unsigned is only for bundles with no signature; one that does
                # not verify is always refused.
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
            # Replaced, not merged, so files dropped between versions do not survive.
            if destination.exists():
                shutil.rmtree(destination)
            shutil.move(str(staged), str(destination))

        return load_manifest(destination / "manifest.toml")

    def is_ready(self, key: str) -> bool:
        """Whether this module could run right now; a directory existing is not
        enough.
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
        Separate from install because it is slow and network-bound. One
        environment per module, so their pins cannot conflict.
        """
        module_dir = self.root / key
        manifest_path = module_dir / "manifest.toml"
        if not manifest_path.is_file():
            raise BundleError(f"{key} is not installed")

        manifest = load_manifest(manifest_path)
        # Vetted before anything runs.
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

        # The bundle ships source: put the vendored backend on the environment's path.
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
        """Create the console script the manifest's `binary` points at; `pip install
        <deps>` does not. A module shipping its own executable declares no
        entrypoint.
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
        """`self.root / key`, proven to be a child of root, because both callers
        rmtree it.
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
