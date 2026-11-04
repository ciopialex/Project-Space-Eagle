"""The voice loop starts even when the screen-control library cannot open the display.

`aethelark_web.runner` imports `main` on a background thread before it opens the
mic. `main` imports the screen tools, and on Linux `import pyautogui` connects
to the X server while it imports. On GNOME Wayland (Ubuntu 24.04 and 26.04) the
old python3-xlib it pulled in could not read Xwayland's auth cookies, so the
import raised DisplayConnectionError. That is not an ImportError, so it got
past every guard and killed the thread. The pill sat idle and never listened.

Without a display, the screen tools are what should go missing. The voice loop
should keep working.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


@pytest.mark.skipif(sys.platform != "linux", reason="the X display is a Linux import-time dependency")
def test_main_imports_when_the_x_display_refuses_the_connection():
    # A display with no server behind it raises the same exception class. Not a
    # fixed number: CI runs its own Xvfb on :99.
    free = next(n for n in range(400, 900)
                if not Path(f"/tmp/.X11-unix/X{n}").exists() and not Path(f"/tmp/.X{n}-lock").exists())
    env = dict(os.environ, DISPLAY=f":{free}")
    env.pop("WAYLAND_DISPLAY", None)
    proc = subprocess.run(
        [sys.executable, "-c",
         "from main import AethelarkLive, _run_core\n"
         "import actions.computer_control as c\n"
         "print('screen tools', c._PYAUTOGUI)"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=180,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    assert "screen tools False" in proc.stdout
