"""`curl | bash` that the network cuts short runs nothing.

bash executes a piped script as it arrives. The installer is one { } group,
so a partial download is a syntax error before any of it runs, rather than
half an install with no message.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO / "install.sh"

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="bash installer")


@pytest.mark.parametrize("fraction", [0.25, 0.5, 0.9])
def test_a_partial_installer_changes_nothing(tmp_path, fraction):
    text = INSTALL_SH.read_bytes()
    partial = text[: int(len(text) * fraction)]
    home = tmp_path / "home"
    home.mkdir()
    env = dict(os.environ, HOME=str(home), TMPDIR=str(tmp_path),
               PATH="/usr/bin:/bin", AETHELARK_NO_LAUNCH="1")
    done = subprocess.run(["bash"], input=partial, env=env,
                          capture_output=True, timeout=30)
    assert done.returncode != 0
    assert list(home.iterdir()) == [], "a partial installer wrote to HOME"


def test_the_installer_checks_the_users_own_path():
    """It adds ~/.local/bin to its own PATH to find uv, so the check for
    whether the user's shell needs it has to look at the PATH it started with."""
    text = INSTALL_SH.read_text(encoding="utf-8")
    assert 'USER_PATH="$PATH"' in text
    assert 'case ":$USER_PATH:" in' in text
