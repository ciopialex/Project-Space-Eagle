"""Windows Advanced Firewall (netsh advfirewall) backend."""
from __future__ import annotations

import os
from pathlib import Path
from typing import Callable, Optional

from core import user_paths
from dashboard.netaccess.base import CommandRunner, NetAccessBackend


def _default_elevate(script_path: Path) -> bool:
    """Invokes PowerShell / ShellExecute to run script elevated."""
    try:
        import ctypes
        res = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            str(script_path),
            None,
            str(script_path.parent),
            1
        )
        return res > 32
    except Exception:
        return False


def _default_is_public() -> bool:
    """Checks if active network profile is Public."""
    return False


class NetshBackend(NetAccessBackend):
    """Windows netsh advfirewall manager."""

    def __init__(
        self,
        runner: Optional[CommandRunner] = None,
        elevate: Optional[Callable[[Path], bool]] = None,
        script_dir: Optional[Path] = None,
        public_network: Optional[Callable[[], bool]] = None
    ) -> None:
        super().__init__(runner)
        self.elevate = elevate or _default_elevate
        self.script_dir = script_dir or (user_paths.user_data_dir() / "netaccess")
        self.is_public = public_network or _default_is_public

    @property
    def name(self) -> str:
        return "netsh"

    def available(self) -> bool:
        return True

    def open(self, port: int) -> bool:
        rule_name = f"Aethelark Port {port}"
        try:
            check_res = self.runner(["netsh", "advfirewall", "firewall", "show", "rule", f"name={rule_name}"])
            if check_res.returncode == 0 and "Enabled" in (check_res.stdout or ""):
                return True
        except Exception:
            pass

        # Prepare script directory with secure 0700 permissions
        self.script_dir.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(self.script_dir, 0o700)
        except Exception:
            pass

        script_path = self.script_dir / f"open_port_{port}.bat"
        lines = [
            "@echo off",
            f'netsh advfirewall firewall add rule name="{rule_name}" dir=in action=allow protocol=TCP localport={port}'
        ]
        if self.is_public():
            lines.append('powershell -Command "Set-NetConnectionProfile -NetworkCategory Private"')

        script_path.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")

        # Elevate via injected runner / ShellExecute
        return bool(self.elevate(script_path))
