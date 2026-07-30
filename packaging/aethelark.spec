# PyInstaller spec for Aethelark.
#
# Build:  pyinstaller packaging/aethelark.spec --noconfirm
#
# onedir, not onefile. onefile re-extracts the whole bundle to a temp directory
# on every launch, and this bundle carries Qt WebEngine - several hundred MB.
# That is seconds of delay each start, and the extraction directory is wiped on
# exit, so anything the app writes beside its own files is silently lost.
# onedir starts fast and is what the platform installers expect to wrap.

import os
import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

BASE = Path(SPECPATH).resolve().parent          # noqa: F821 - PyInstaller global
IS_WIN = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"

# Everything the app opens by relative path at runtime. aethelark_web.py builds
# these from Path(__file__).parent, which under a frozen build resolves to the
# bundle root - so the layout here has to mirror the repo exactly.
datas = [
    (str(BASE / "web" / "dashboard.html"),   "web"),
    (str(BASE / "web" / "pill.html"),        "web"),
    (str(BASE / "web" / "onboarding.html"),  "web"),
    (str(BASE / "assets" / "fonts"),         "assets/fonts"),
    (str(BASE / "assets" / "images"),        "assets/images"),
    (str(BASE / "dashboard" / "static"),     "dashboard/static"),
    (str(BASE / "config" / "aethelark.ico"), "config"),
]

# memory/long_term.json ships as the seed for a fresh install. The running app
# must never write back to it - see packaging/runtime_paths.py.
seed = BASE / "memory" / "long_term.json"
if seed.exists():
    datas.append((str(seed), "memory"))

# google-genai and playwright both load resources through importlib rather than
# plain imports, so PyInstaller's static analysis cannot see them.
for pkg in ("google.genai", "google.generativeai", "playwright"):
    try:
        datas += collect_data_files(pkg)
    except Exception:
        pass  # optional at build time; absence is caught by the smoke test

hiddenimports = [
    # Qt: WebEngine and WebChannel are pulled in dynamically by QWebEngineView.
    "PyQt6.QtWebEngineWidgets",
    "PyQt6.QtWebEngineCore",
    "PyQt6.QtWebChannel",
    "PyQt6.QtNetwork",
    "PyQt6.QtPrintSupport",
    # Backend surface reached through late imports in actions/.
    "sounddevice",
    "psutil",
    "mss",
    "pyte",
]
hiddenimports += collect_submodules("google.genai")

if IS_WIN:
    hiddenimports += ["comtypes", "pycaw", "win32com.client", "winpty"]
elif IS_MAC:
    hiddenimports += ["objc", "ApplicationServices"]

a = Analysis(                                    # noqa: F821
    [str(BASE / "aethelark_web.py")],
    pathex=[str(BASE)],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    # Playwright's browser binaries are ~400MB and are fetched on first run by
    # runtime_paths.ensure_browsers(), not shipped. Bundling them would triple
    # the download for a feature most users never reach.
    excludes=["tkinter", "pytest", "matplotlib", "PyQt5", "PySide6"],
    noarchive=False,
)

pyz = PYZ(a.pure)                                # noqa: F821

exe = EXE(                                       # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Aethelark",
    debug=False,
    strip=False,
    upx=False,          # UPX corrupts Qt plugin loading on Windows
    console=False,      # GUI app: no console window
    icon=str(BASE / "config" / "aethelark.ico") if IS_WIN else None,
)

coll = COLLECT(                                  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="Aethelark",
)

if IS_MAC:
    app = BUNDLE(                                # noqa: F821
        coll,
        name="Aethelark.app",
        icon=None,      # set once an .icns exists; see packaging/README.md
        bundle_identifier="com.aethelark.desktop",
        info_plist={
            "CFBundleShortVersionString": os.environ.get("AE_VERSION", "0.0.0"),  # noqa: F821
            "NSHighResolutionCapable": True,
            # The app watches the screen, listens, and drives other apps. macOS
            # requires a stated purpose for each or it kills the process.
            "NSMicrophoneUsageDescription":
                "Aethelark listens for your voice commands.",
            "NSCameraUsageDescription":
                "Aethelark can see your screen to help with what you are doing.",
            "NSAppleEventsUsageDescription":
                "Aethelark controls other applications on your behalf.",
            "LSMinimumSystemVersion": "12.0",
        },
    )
