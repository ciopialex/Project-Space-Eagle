"""Cross-platform network and firewall access manager."""
from __future__ import annotations

import platform
from typing import List, Optional

from dashboard.netaccess.base import CommandRunner, NetAccessBackend
from dashboard.netaccess.linux import FirewalldBackend, IptablesBackend, UfwBackend
from dashboard.netaccess.macos import SocketFilterBackend
from dashboard.netaccess.windows import NetshBackend


def backends_for(platform_name: str, runner: Optional[CommandRunner] = None) -> List[NetAccessBackend]:
    """Resolves ordered list of firewall backends for target OS."""
    p = platform_name.lower().strip()
    if p in ["darwin", "mac", "macos"]:
        return [SocketFilterBackend(runner=runner)]
    if p in ["windows", "win32", "cygwin"]:
        return [NetshBackend(runner=runner)]
    # Default to Linux backends in preference order: ufw -> firewalld -> iptables
    return [
        UfwBackend(runner=runner),
        FirewalldBackend(runner=runner),
        IptablesBackend(runner=runner)
    ]


def ensure_open(
    port: int,
    *,
    runner: Optional[CommandRunner] = None,
    platform_name: Optional[str] = None
) -> bool:
    """
    Attempts to ensure network access for the given port across OS backends.
    Never raises an exception; returns True on first success, False on failure.
    """
    plat = platform_name or platform.system()
    try:
        backends = backends_for(plat, runner=runner)
        for backend in backends:
            try:
                if backend.available():
                    if backend.open(port):
                        return True
            except Exception:
                continue
    except Exception:
        pass
    return False
