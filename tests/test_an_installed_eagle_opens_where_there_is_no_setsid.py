"""An installed eagle opens on a machine without setsid (macOS ships none).

The launcher detaches the app from the terminal. It used to do that with
setsid alone, inside a silenced background job, so on macOS `eagle` printed
"opening" and nothing ever started.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "packaging" / "eagle"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash launcher")


def _fake_install(tmp_path: Path) -> Path:
    app = tmp_path / "app"
    (app / "packaging").mkdir(parents=True)
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".aethelark-install").touch()
    marker = tmp_path / "started"
    # The app: proves it ran by leaving a file behind.
    (app / "aethelark_web.py").write_text(
        f"open({str(marker)!r}, 'w').write('ok')\n", encoding="utf-8")
    (app / ".venv" / "bin" / "activate").write_text("", encoding="utf-8")
    rendered = LAUNCHER.read_text(encoding="utf-8").replace(
        "@AETHELARK" "_HOME@", str(app))
    (app / "packaging" / "eagle").write_text(rendered, encoding="utf-8")
    launcher = tmp_path / "eagle"
    launcher.write_text(rendered, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _path_without(tmp_path: Path, missing: str) -> str:
    """A PATH holding every tool the launcher needs except `missing`."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "env", "sed", "cmp", "cat", "chmod", "rm", "mktemp",
                 "readlink", "nohup", "pgrep", "git", "uname", "dirname", "seq",
                 "sleep", "kill", "tr", "wc", "mkdir", "date", "cut", "tee"):
        found = shutil.which(tool)
        if found and tool != missing:
            (bin_dir / tool).symlink_to(found)
    (bin_dir / "python3").symlink_to(sys.executable)
    return str(bin_dir)


def test_plain_eagle_starts_the_app_when_setsid_is_missing(tmp_path):
    launcher = _fake_install(tmp_path)
    env = dict(os.environ, PATH=_path_without(tmp_path, "setsid"))
    env.pop("EAGLE_LOG", None)
    done = subprocess.run(["bash", str(launcher)], env=env, capture_output=True,
                          text=True, timeout=30)
    assert done.returncode == 0, done.stderr
    assert "opening" in done.stdout
    marker = tmp_path / "started"
    for _ in range(100):
        if marker.exists():
            break
        subprocess.run(["sleep", "0.05"])
    assert marker.exists(), textwrap.shorten(done.stdout + done.stderr, 400)
