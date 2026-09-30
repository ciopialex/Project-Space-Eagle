"""Make the OS accessibility layer reachable from inside the virtualenv.

The eagle's fast path — reading an app's real interface structure instead of
screenshotting it and asking a vision model — needs the platform accessibility
bindings.

On Windows and macOS those are ordinary pip installs. On Linux, PyGObject is a
*system* package (`python3-gi`): it ships with every GNOME desktop and cannot
be pip-installed without compiling against girepository and cairo headers. So
on a typical Linux machine the bindings are already present and the venv simply
cannot see them.

This module pokes a hole for exactly that one package. It is deliberately
narrow: it links only the named packages, only into a virtualenv, never over an
existing real directory, only when the C-extension ABI matches, and it never
raises. If any of that fails, grounding degrades to vision — slower, but
working.
"""
from __future__ import annotations

import os
import sys
import sysconfig
from pathlib import Path
from typing import Callable, Iterable

# Packages PyGObject needs on Linux. `cairo` is optional but several gi
# submodules import it eagerly.
_LINUX_PACKAGES = ("gi", "cairo")

_APT_HINT = (
    "Structural grounding is unavailable, so the eagle is falling back to "
    "slower vision lookups. On Debian/Ubuntu install the system bindings with:"
    "\n    sudo apt install python3-gi gir1.2-atspi-2.0\n"
    "On Fedora:\n    sudo dnf install python3-gobject"
)


def _default_candidates(package: str) -> list[Path]:
    """Where distributions put system Python packages."""
    version = f"python{sys.version_info.major}.{sys.version_info.minor}"
    roots = [
        Path("/usr/lib/python3/dist-packages"),          # Debian / Ubuntu
        Path(f"/usr/lib/{version}/site-packages"),       # Arch / Fedora
        Path(f"/usr/lib64/{version}/site-packages"),     # Fedora 64-bit
        Path(f"/usr/local/lib/{version}/dist-packages"),
    ]
    return [root / package for root in roots]


def find_system_package(package: str,
                        candidates: Iterable[Path] | None = None) -> Path | None:
    """First existing system location for `package`, or None."""
    for candidate in (candidates if candidates is not None
                      else _default_candidates(package)):
        try:
            if Path(candidate).is_dir():
                return Path(candidate)
        except Exception:
            continue
    return None


def abi_matches(package_dir: Path, suffix: str) -> bool:
    """Do this package's C extensions match our interpreter's ABI?

    A gi built for CPython 3.9 will not load into CPython 3.12. Packages with
    no extension modules have no ABI to disagree about, so they pass.
    """
    try:
        extensions = list(Path(package_dir).glob("*.so"))
    except Exception:
        return False
    if not extensions:
        return True
    return any(name.endswith(suffix) for name in (e.name for e in extensions))


def venv_site_packages() -> Path | None:
    """This venv's site-packages, or None when not running inside a venv."""
    if sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        return None
    try:
        return Path(sysconfig.get_paths()["purelib"])
    except Exception:
        return None


def link_into(source: Path, site_packages: Path, name: str,
              symlink: Callable[[Path, Path], None] = os.symlink) -> bool:
    """Symlink `source` into `site_packages/name`. Never clobbers, never raises."""
    target = Path(site_packages) / name
    try:
        if target.is_symlink():
            return True                      # already linked; idempotent
        if target.exists():
            return False                     # a real package lives there
        symlink(source, target)
        return True
    except Exception:
        return False


def ensure_accessibility(*,
                         importer: Callable[[str], bool] | None = None,
                         finder: Callable[[str], Path | None] | None = None,
                         site_packages: Callable[[], Path | None] | None = None,
                         symlink: Callable[[Path, Path], None] = os.symlink,
                         packages: Iterable[str] = _LINUX_PACKAGES) -> dict:
    """Make the accessibility bindings importable if we possibly can.

    Returns {"ok", "method", "detail"} where method is one of
    "already-importable", "linked", "not-needed", "unavailable".
    """
    def _can_import(name: str) -> bool:
        try:
            __import__(name)
            return True
        except Exception:
            return False

    importer = importer or _can_import

    try:
        if importer("gi"):
            return {"ok": True, "method": "already-importable",
                    "detail": "accessibility bindings already importable"}

        # Windows and macOS get their bindings from pip; there is nothing to link.
        if sys.platform not in ("linux", "linux2") and finder is None:
            return {"ok": False, "method": "not-needed",
                    "detail": f"no system-package linking needed on {sys.platform}"}

        finder = finder or find_system_package
        site_fn = site_packages or venv_site_packages
        site = site_fn()
        if site is None:
            return {"ok": False, "method": "unavailable",
                    "detail": "not running inside a virtualenv; " + _APT_HINT}

        suffix = (sysconfig.get_config_var("EXT_SUFFIX") or ".so")
        linked_any = False
        for package in packages:
            source = finder(package)
            if source is None:
                continue
            if not abi_matches(source, suffix):
                continue
            if link_into(source, site, package, symlink=symlink):
                linked_any = True

        if linked_any and importer("gi"):
            return {"ok": True, "method": "linked",
                    "detail": f"linked system accessibility bindings into {site}"}

        return {"ok": False, "method": "unavailable", "detail": _APT_HINT}
    except Exception as e:
        return {"ok": False, "method": "unavailable",
                "detail": f"{_APT_HINT}\n(bootstrap failed: {e})"}
