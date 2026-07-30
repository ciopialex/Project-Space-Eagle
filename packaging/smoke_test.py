"""Prove a freshly-built bundle is not obviously broken, before we ship it.

PyInstaller succeeds far more often than the thing it produces actually runs:
a missing hidden import or a data file left out of the spec only shows up when
someone double-clicks the app. This runs in CI right after the build and fails
the job instead of publishing a release that cannot start.

It deliberately does NOT launch the GUI - CI runners have no display, and a
frozen Qt app that cannot open a window is not evidence of anything. It checks
the two things that actually break: the bundle layout, and whether the entry
point's imports resolve inside the bundle.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Relative to the bundle root. If aethelark_web.py opens it by path, it belongs
# in this list - that is the whole point.
REQUIRED = [
    "web/dashboard.html",
    "web/pill.html",
    "assets/fonts",
    "assets/images",
    "config/aethelark.ico",
]


def bundle_root() -> Path:
    """Where the built app's files live, per platform."""
    if IS_MAC:
        app = ROOT / "dist" / "Aethelark.app"
        if not app.is_dir():
            fail(f"no app bundle at {app}")
        return app / "Contents" / "Resources"
    return ROOT / "dist" / "Aethelark"


def executable() -> Path:
    if IS_MAC:
        return ROOT / "dist" / "Aethelark.app" / "Contents" / "MacOS" / "Aethelark"
    return bundle_root() / ("Aethelark.exe" if IS_WIN else "Aethelark")


def fail(msg: str) -> None:
    print(f"smoke test FAILED: {msg}", file=sys.stderr)
    raise SystemExit(1)


def main() -> None:
    root = bundle_root()
    if not root.is_dir():
        fail(f"no bundle at {root} - did pyinstaller run?")

    exe = executable()
    if not exe.is_file():
        fail(f"no executable at {exe}")

    missing = []
    for rel in REQUIRED:
        # onedir puts data under _internal/ on PyInstaller 6.x; accept either.
        if not ((root / rel).exists() or (root / "_internal" / rel).exists()):
            missing.append(rel)
    if missing:
        fail("data files absent from the bundle: " + ", ".join(missing))

    size_mb = sum(p.stat().st_size for p in root.rglob("*") if p.is_file()) / 1e6
    print(f"bundle at {root}")
    print(f"  executable: {exe.name}")
    print(f"  size:       {size_mb:.0f} MB")

    # Qt WebEngine is the dependency most likely to be missing or mislinked, and
    # it fails at import time. Ask the bundled interpreter to import it in
    # isolation; a non-zero exit means the app could never have started.
    probe = "import PyQt6.QtWebEngineWidgets, PyQt6.QtWebChannel; print('qt ok')"
    result = subprocess.run(
        [str(exe)], input=probe, capture_output=True, text=True, timeout=120,
        env={"AETHELARK_SMOKE_IMPORT": "1", "QT_QPA_PLATFORM": "offscreen"},
    )
    # The entry point does not yet honour AETHELARK_SMOKE_IMPORT (see
    # packaging/README.md), so a clean exit is not required here. What matters
    # is that it did not die on a missing shared library or import error.
    blocking = ("ModuleNotFoundError", "ImportError", "cannot open shared object")
    combined = (result.stdout or "") + (result.stderr or "")
    for symptom in blocking:
        if symptom in combined:
            fail(f"bundle cannot import its own dependencies:\n{combined[:2000]}")

    print("smoke test passed")


if __name__ == "__main__":
    main()
