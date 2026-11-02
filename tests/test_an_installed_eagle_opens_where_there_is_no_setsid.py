"""The installed launcher opens the app without setsid (macOS has none), and
says why when the app dies as it starts."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "packaging" / "eagle"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash launcher")


def _fake_install(tmp_path: Path, app_source: str) -> Path:
    app = tmp_path / "app"
    (app / "packaging").mkdir(parents=True)
    (app / ".venv" / "bin").mkdir(parents=True)
    (app / ".aethelark-install").touch()
    (app / "aethelark_web.py").write_text(app_source, encoding="utf-8")
    (app / ".venv" / "bin" / "activate").write_text("", encoding="utf-8")
    rendered = (LAUNCHER.read_text(encoding="utf-8")
                .replace("@AETHELARK" "_HOME@", str(app))
                .replace("aethelark_web\\.py'", "never-matches-a-real-eagle'"))
    (app / "packaging" / "eagle").write_text(rendered, encoding="utf-8")
    launcher = tmp_path / "eagle"
    launcher.write_text(rendered, encoding="utf-8")
    launcher.chmod(0o755)
    return launcher


def _env_without(tmp_path: Path, missing: str) -> dict:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("bash", "env", "sed", "cmp", "cat", "chmod", "rm", "mktemp", "grep",
                 "readlink", "nohup", "pgrep", "git", "uname", "dirname", "seq", "id",
                 "sleep", "kill", "tr", "wc", "mkdir", "date", "cut", "tee", "tail", "setsid"):
        found = shutil.which(tool)
        if found and tool != missing:
            (bin_dir / tool).symlink_to(found)
    (bin_dir / "python3").symlink_to(sys.executable)
    env = dict(os.environ, PATH=str(bin_dir), QT_QPA_PLATFORM="offscreen")
    env.pop("EAGLE_LOG", None)
    return env


def test_plain_eagle_starts_the_app_when_setsid_is_missing(tmp_path):
    marker = tmp_path / "started"
    launcher = _fake_install(
        tmp_path, f"import time\nopen({str(marker)!r}, 'w').write('ok')\ntime.sleep(5)\n")
    done = subprocess.run(["bash", str(launcher)], env=_env_without(tmp_path, "setsid"),
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "opening" in done.stdout
    assert marker.exists()


def test_an_app_that_dies_on_start_is_reported(tmp_path):
    launcher = _fake_install(
        tmp_path, "import sys\nprint('Could not load the Qt platform plugin')\nsys.exit(3)\n")
    started = time.monotonic()
    done = subprocess.run(["bash", str(launcher)], env=_env_without(tmp_path, ""),
                          capture_output=True, text=True, timeout=30)
    assert done.returncode == 1
    assert "Could not load the Qt platform plugin" in done.stderr
    assert "--doctor" in done.stderr
    assert time.monotonic() - started < 10
