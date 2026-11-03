"""What is missing on this machine, and the exact command that fixes it.

1. Report only what was checked; UNKNOWN is not OK.
2. Give the fix for this platform.
3. Never claim a fix without re-checking.

Some things cannot be automated: macOS requires the user to grant Accessibility
and Screen Recording by hand.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from core import echo_cancel

OK, MISSING, UNKNOWN, MANUAL = "OK", "MISSING", "UNKNOWN", "NEEDS YOU"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""          # a command that can be run
    auto: bool = False     # safe for --fix to run unattended
    # The fix changes a machine-wide setting (desktop accessibility, every app's audio):
    # --fix asks first and never runs one unattended.
    system: bool = False

    @property
    def good(self) -> bool:
        return self.status == OK


def _pip(packages: str) -> str:
    """A command that installs into THIS environment."""
    uv = shutil.which("uv") or next(
        (str(p) for p in (Path.home() / ".local/bin/uv", Path.home() / ".cargo/bin/uv")
         if p.is_file()), None)
    if uv:
        return f'"{uv}" pip install --python "{sys.executable}" {packages}'
    return f'"{sys.executable}" -m pip install {packages}'


def _plat() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _module(name: str, pip: str, why: str) -> Check:
    import importlib.util
    import warnings
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            importlib.import_module(name)
        return Check(why, OK)
    except Exception as e:
        try:
            installed = importlib.util.find_spec(name) is not None
        except Exception:
            installed = False
        if not installed:
            return Check(why, MISSING, str(e)[:70], fix=_pip(pip), auto=True)
        if isinstance(e, KeyError) and str(e).strip("'\"") in ("DISPLAY", "WAYLAND_DISPLAY"):
            return Check(why, UNKNOWN, "no desktop session here",
                         fix="run eagle --doctor from a terminal on your desktop")
        return Check(why, MISSING, f"installed but would not start: {str(e)[:60]}")


def _structural_grounding() -> Check:
    """The fast, exact way the eagle finds controls; vision is the slow fallback."""
    plat = _plat()
    if plat == "linux":
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401
        except Exception as e:
            return Check(
                "structural grounding (AT-SPI)", MISSING, str(e)[:70],
                # PyGObject ships no wheels and the app's environment cannot see
                # python3-gi, so it builds from these headers.
                fix=("sudo apt install gir1.2-atspi-2.0 libgirepository-2.0-dev "
                     "libcairo2-dev pkg-config python3-dev && "
                     + _pip("PyGObject")))
        # The binding importing is not enough: GNOME's toolkit-accessibility toggle
        # gates whether any app publishes to the bus.
        try:
            out = subprocess.run(
                ["gsettings", "get", "org.gnome.desktop.interface",
                 "toolkit-accessibility"],
                capture_output=True, text=True, timeout=3)
            if out.stdout.strip() != "true":
                return Check(
                    "structural grounding (AT-SPI)", MISSING,
                    "toolkit-accessibility is off — the bus runs but "
                    "nothing publishes to it",
                    fix="gsettings set org.gnome.desktop.interface "
                        "toolkit-accessibility true", auto=True, system=True)
        except Exception:
            pass
                   # Not a GNOME desktop: the import already proved the binding works.
        return Check("structural grounding (AT-SPI)", OK)
    if plat == "windows":
        try:
            import comtypes  # noqa: F401
            return Check("structural grounding (UI Automation)", OK)
        except Exception as e:
            return Check("structural grounding (UI Automation)", MISSING,
                         str(e)[:70],
                         fix=_pip("comtypes pywin32"),
                         auto=True)
    try:
        import ApplicationServices  # noqa: F401
        return Check("structural grounding (macOS AX)", OK)
    except Exception as e:
        return Check("structural grounding (macOS AX)", MISSING, str(e)[:70],
                     fix=_pip("pyobjc-framework-ApplicationServices"), auto=True)


def _os_permission() -> Check:
    """The one gate no installer can pass for you."""
    plat = _plat()
    if plat != "macos":
        # Wayland restricts synthetic input too.
        if plat == "linux" and os.environ.get("WAYLAND_DISPLAY"):
            return Check(
                "input permission", MANUAL,
                "Wayland blocks synthetic mouse/keyboard for ordinary apps",
                fix="log in with an X11/Xorg session, or expect keyboard and "
                    "mouse control to be refused by the compositor")
        return Check("input permission", OK, f"{plat}: not gated")
    try:
        from ApplicationServices import AXIsProcessTrusted
        if AXIsProcessTrusted():
            return Check("input permission (macOS Accessibility)", OK)
        return Check(
            "input permission (macOS Accessibility)", MANUAL,
            "macOS will silently ignore every click and keystroke until granted",
            fix="System Settings → Privacy & Security → Accessibility → "
                "enable Aethelark (and the same under Screen Recording)")
    except Exception as e:
        return Check("input permission (macOS Accessibility)", UNKNOWN,
                     str(e)[:70])


def _hidden_display() -> Check:
    """Whether the browser can be headed without being seen (Xvfb). Sites reject
    headless browsers; without Xvfb the eagle still works but loses to those
    sites.
    """
    from core import virtual_display
    if virtual_display.available():
        return Check("private display (headed but unseen)", OK)
    if _plat() != "linux":
        return Check("private display (headed but unseen)", OK,
                     f"{_plat()}: not applicable")
    return Check(
        "private display (headed but unseen)", MISSING,
        "without it the browser runs headless, which some sites refuse",
        fix="sudo apt install xvfb")


def _browser() -> Check:
    try:
        from playwright.sync_api import sync_playwright  # noqa: F401
    except Exception:
        return Check("browser engine", MISSING, "playwright not installed",
                     fix=_pip("playwright"), auto=True)
    roots = [Path.home() / ".cache/ms-playwright",
             Path.home() / "Library/Caches/ms-playwright",
             Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"]
    for r in roots:
        try:
            if r.exists() and any(r.iterdir()):
                return Check("browser engine", OK, str(r))
        except Exception:
            continue
    return Check("browser engine", MISSING, "no downloaded browser found",
                 fix=f"{sys.executable} -m playwright install chromium",
                 auto=True)


def _api_key() -> Check:
    try:
        from core import user_paths
        p = Path(user_paths.api_keys_path())
    except Exception as e:
        return Check("API key", UNKNOWN, str(e)[:70])
    if not p.exists():
        return Check("API key", MISSING, f"no {p.name}",
                     fix="run Aethelark once and complete onboarding")
    try:
        import json
        key = (json.loads(p.read_text()) or {}).get("gemini_api_key") or ""
        if not str(key).strip():
            return Check("API key", MISSING, "gemini_api_key is empty",
                         fix="start the eagle and finish setup")
    except Exception as e:
        return Check("API key", UNKNOWN, str(e)[:70])
    # A saved key is not an accepted one until Google says so.
    from core import gemini_key
    ok, why = gemini_key.check(key)
    if ok:
        return Check("API key", OK, "Google accepts it")
    if "reach Google" in why:
        return Check("API key", UNKNOWN, why)
    return Check("API key", MISSING, why,
                 fix="open the eagle's Settings and paste a new key under Voice")


def _audio() -> Check:
    try:
        import sounddevice as sd
    except OSError:
        # The Python package is there; the system library it wraps is not.
        fix = {"linux": "sudo apt install libportaudio2   (Fedora: sudo dnf install portaudio)",
               }.get(_plat(), _pip("--force-reinstall sounddevice"))
        return Check("audio in/out", MISSING, "the PortAudio library is missing",
                     fix=fix)
    except Exception as e:
        return Check("audio in/out", MISSING, str(e)[:70],
                     fix=_pip("sounddevice"), auto=True)
    try:
        outs = [d for d in sd.query_devices() if d.get("max_output_channels", 0)]
        ins = [d for d in sd.query_devices() if d.get("max_input_channels", 0)]
        if not ins:
            return Check("microphone", MISSING, "no input device",
                         fix="plug in or enable a microphone")
        if not outs:
            return Check("speakers", MISSING, "no output device",
                         fix="enable an audio output device")
        return Check("audio in/out", OK, f"{len(ins)} in, {len(outs)} out")
    except Exception as e:
        return Check("audio in/out", UNKNOWN, str(e)[:70])


def _echo_cancel() -> Check:
    """Whether the microphone is deaf to the speakers. Read-only: restarts nothing.
    supported=False is OK on Windows and macOS (the OS does it) and UNKNOWN on
    Linux, never MISSING with a fix that cannot work.
    """
    plat = _plat()
    try:
        st = echo_cancel.state()
    except Exception as e:
        return Check("echo cancellation", UNKNOWN, str(e)[:70])
    if plat != "linux":
        # Not OK: nothing on these platforms cancels the eagle's own voice.
        return Check("echo cancellation", MANUAL, st.detail,
                     fix="use headphones to talk over the eagle at a normal voice")
    if not st.supported:
        return Check("echo cancellation", UNKNOWN, st.detail)
    if st.active:
        return Check("echo cancellation", OK, st.detail)
    return Check("echo cancellation", MISSING, st.detail,
                 fix=f"{sys.executable} -m core.echo_cancel --install",
                 auto=True, system=True)


def _trash() -> Check:
    c = _module("send2trash", "send2trash", "safe delete (trash, not erase)")
    return c


def _domain_modules() -> Check:
    """Which domain modules are plugged in. Never MISSING: zero modules is normal. A
    declared but absent module is worth a line.
    """
    try:
        from core.module_bus import ModuleBus
        bus = ModuleBus().discover()
    except Exception as e:
        return Check("domain modules", UNKNOWN, str(e)[:70])

    live = bus.available()
    gone = bus.absent()

    if not live and not gone:
        return Check("domain modules", OK,
                     "none installed — add one with: eagle install trade | 3d")
    if not live:
        names = ", ".join(m.key for m in gone)
        return Check("domain modules", OK,
                     f"declared but not installed: {names}")

    detail = ", ".join(f"{m.key} ({len(m.tools)} tools)" for m in live)
    if gone:
        detail += f" — not installed: {', '.join(m.key for m in gone)}"
    return Check("domain modules", OK, detail)


def run_checks() -> list[Check]:
    return [
        _api_key(),
        _browser(),
        _hidden_display(),
        _structural_grounding(),
        _os_permission(),
        _audio(),
        _echo_cancel(),
        _module("mss", "mss", "screen capture"),
        _module("pyautogui", "pyautogui", "mouse and keyboard"),
        _module("google.genai", "google-genai", "the brain"),
        _module("PyQt6.QtWebEngineWidgets", "PyQt6 PyQt6-WebEngine",
                "the window"),
        _trash(),
        _domain_modules(),
    ]


def apply_fixes(checks: list[Check], run=subprocess.run,
                ask=None) -> list[Check]:
    """Run the fixes that are safe unattended, then re-check."""
    if ask is None:
        def ask(question: str) -> bool:
            if not sys.stdin.isatty():
                return False
            return input(f"  {question} [y/N] ").strip().lower() in ("y", "yes")
    for c in checks:
        if c.good or not c.auto or not c.fix:
            continue
        if c.system and not ask(f"{c.name}: this changes a setting for your "
                                f"whole computer ({c.fix}). Do it?"):
            print(f"  – skipped {c.name}; run it yourself: {c.fix}")
            continue
        print(f"  → {c.fix}")
        try:
            run(c.fix, shell=True, check=False)
        except Exception as e:
            print(f"    failed: {e}")
    return run_checks()


def report(checks: list[Check]) -> str:
    width = max(len(c.name) for c in checks)
    lines = []
    for c in checks:
        mark = {OK: "✓", MISSING: "✗", MANUAL: "!", UNKNOWN: "?"}[c.status]
        lines.append(f"  {mark} {c.name:<{width}}  {c.status}"
                     + (f"  — {c.detail}" if c.detail else ""))
        if not c.good and c.fix:
            lines.append(f"      fix: {c.fix}")
    broken = [c for c in checks if not c.good]
    lines.append("")
    if not broken:
        lines.append(f"  Everything checked is working on {_plat()}.")
    else:
        auto = sum(1 for c in broken if c.auto)
        lines.append(f"  {len(broken)} need attention on {_plat()}"
                     + (f"; {auto} can be fixed automatically with --fix."
                        if auto else "."))
    return "\n".join(lines).replace(str(Path.home()), "~")


def main(argv=None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    print(f"\nAethelark preflight — {_plat()}, python {sys.version.split()[0]}\n")
    checks = run_checks()
    if "--fix" in argv:
        print(report(checks))
        print("\nApplying the fixes that are safe to run unattended:\n")
        checks = apply_fixes(checks)
        print("\nAfter fixing:\n")
    print(report(checks))
    print()
    return 0 if all(c.good for c in checks) else 1


if __name__ == "__main__":
    raise SystemExit(main())
