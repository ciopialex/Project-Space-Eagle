"""What is missing, on THIS machine, and the exact command that fixes it.

There was no preflight. A new install on any OS discovers what it needs one
crash at a time, in whatever order it happens to reach them, and the failures
do not name themselves: a missing Playwright browser looks like "the web tool
is broken", missing AT-SPI bindings look like "it cannot see anything", and a
missing macOS Accessibility grant looks like the mouse is simply ignored.

Three rules this follows, because the alternative is what the codebase already
has too much of:

1. **Report only what was actually checked.** No claim of health for a thing
   that was skipped. `UNKNOWN` is a legitimate outcome and is not `OK`.
2. **Say the fix for the platform you are on**, not a generic one. `apt` on a
   Mac is not a fix, it is noise.
3. **Never claim to have fixed something without re-checking it.** Everything
   this session went wrong on was a report written before the verification.

Some things cannot be automated and saying so is part of the job: macOS
requires the user to grant Accessibility and Screen Recording by hand, and no
amount of engineering removes that dialog.
"""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

OK, MISSING, UNKNOWN, MANUAL = "OK", "MISSING", "UNKNOWN", "NEEDS YOU"


@dataclass
class Check:
    name: str
    status: str
    detail: str = ""
    fix: str = ""          # a command that can be run
    auto: bool = False     # safe for --fix to run unattended

    @property
    def good(self) -> bool:
        return self.status == OK


def _plat() -> str:
    if sys.platform.startswith("win"):
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def _module(name: str, pip: str, why: str) -> Check:
    try:
        importlib.import_module(name)
        return Check(why, OK)
    except Exception as e:
        return Check(why, MISSING, str(e)[:70],
                     fix=f"{sys.executable} -m pip install {pip}", auto=True)


def _structural_grounding() -> Check:
    """The fast, exact way the eagle finds controls. Vision is the slow, and
    much less accurate, fallback — measured at 5.8s and 650px off."""
    plat = _plat()
    if plat == "linux":
        try:
            import gi
            gi.require_version("Atspi", "2.0")
            from gi.repository import Atspi  # noqa: F401
        except Exception as e:
            return Check(
                "structural grounding (AT-SPI)", MISSING, str(e)[:70],
                fix="sudo apt install python3-gi gir1.2-atspi-2.0 "
                    "&& sudo systemctl --user restart at-spi-dbus-bus")
        # The binding importing is necessary but not sufficient. GNOME's own
        # toggle gates whether ANY app publishes to the bus at all — found
        # live: the bus and both its daemons were running, this import
        # succeeded, and every screen_click still failed, because only this
        # one setting was off.
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
                        "toolkit-accessibility true", auto=True)
        except Exception:
            pass   # not a GNOME desktop (no gsettings) — the import already
                   # proved the binding works; do not fail a check this
                   # function cannot honestly evaluate here
        return Check("structural grounding (AT-SPI)", OK)
    if plat == "windows":
        try:
            import comtypes  # noqa: F401
            return Check("structural grounding (UI Automation)", OK)
        except Exception as e:
            return Check("structural grounding (UI Automation)", MISSING,
                         str(e)[:70],
                         fix=f"{sys.executable} -m pip install comtypes pywin32",
                         auto=True)
    try:
        import ApplicationServices  # noqa: F401
        return Check("structural grounding (macOS AX)", OK)
    except Exception as e:
        return Check("structural grounding (macOS AX)", MISSING, str(e)[:70],
                     fix=f"{sys.executable} -m pip install "
                         "pyobjc-framework-ApplicationServices", auto=True)


def _os_permission() -> Check:
    """The one gate no installer can pass for you."""
    plat = _plat()
    if plat != "macos":
        # Wayland restricts synthetic input the same way, and is worth saying
        # out loud rather than discovering through a mouse that never moves.
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
    """Whether the browser can be headed without being seen.

    Headless is what sites fingerprint — measured on makerworld.com, headless
    got a Cloudflare interstitial every time and the identical browser headed
    loaded the page. Xvfb gives a display nobody is looking at, so the browser
    can be accepted AND invisible. Without it the eagle still works; it just
    loses to the sites that check.
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
                     fix=f"{sys.executable} -m pip install playwright", auto=True)
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
                         fix="run onboarding, or paste a key into " + str(p))
        return Check("API key", OK)
    except Exception as e:
        return Check("API key", UNKNOWN, str(e)[:70])


def _audio() -> Check:
    try:
        import sounddevice as sd
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


def _trash() -> Check:
    c = _module("send2trash", "send2trash", "safe delete (trash, not erase)")
    return c


def run_checks() -> list[Check]:
    return [
        _api_key(),
        _browser(),
        _hidden_display(),
        _structural_grounding(),
        _os_permission(),
        _audio(),
        _module("mss", "mss", "screen capture"),
        _module("pyautogui", "pyautogui", "mouse and keyboard"),
        _module("google.genai", "google-genai", "the brain"),
        _module("PyQt6.QtWebEngineWidgets", "PyQt6 PyQt6-WebEngine",
                "the window"),
        _trash(),
    ]


def apply_fixes(checks: list[Check], run=subprocess.run) -> list[Check]:
    """Run the fixes that are safe unattended, then RE-CHECK.

    Re-checking is not politeness. Everything that went wrong in this codebase
    went wrong by reporting before verifying.
    """
    for c in checks:
        if c.good or not c.auto or not c.fix:
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
    return "\n".join(lines)


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
