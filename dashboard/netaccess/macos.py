"""macOS Application Firewall (socketfilterfw) backend."""
from __future__ import annotations

import sys
from typing import Optional

from dashboard.netaccess.base import CommandRunner, NetAccessBackend


class SocketFilterBackend(NetAccessBackend):
    """macOS Application Layer Firewall backend."""

    def __init__(
        self,
        runner: Optional[CommandRunner] = None,
        python_exe: Optional[str] = None
    ) -> None:
        super().__init__(runner)
        self.python_exe = python_exe or sys.executable

    @property
    def name(self) -> str:
        return "socketfilterfw"

    def available(self) -> bool:
        try:
            res = self.runner(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--getglobalstate"])
            return res.returncode == 0 and "enabled" in (res.stdout or "").lower()
        except Exception:
            return False

    def open(self, port: int) -> bool:
        # Check if python app is already listed
        try:
            list_res = self.runner(["/usr/libexec/ApplicationFirewall/socketfilterfw", "--listapps"])
            if self.python_exe in (list_res.stdout or ""):
                return True
        except Exception:
            pass

        # Add binary with osascript admin prompt
        script = f'do shell script "/usr/libexec/ApplicationFirewall/socketfilterfw --add {self.python_exe} --unblockapp {self.python_exe}" with administrator privileges'
        try:
            res = self.runner(["osascript", "-e", script])
            return res.returncode == 0
        except Exception:
            return False
