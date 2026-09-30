"""Linux firewall backends: UFW, Firewalld, and IPTables."""
from __future__ import annotations

from typing import Optional

from dashboard.netaccess.base import CommandRunner, NetAccessBackend


class UfwBackend(NetAccessBackend):
    """Ubuntu/Debian Uncomplicated Firewall."""

    @property
    def name(self) -> str:
        return "ufw"

    def available(self) -> bool:
        try:
            res = self.runner(["ufw", "status"])
            return res.returncode == 0 and "Status: active" in (res.stdout or "")
        except Exception:
            return False

    def open(self, port: int) -> bool:
        attempts = [
            ["pkexec", "ufw", "allow", f"{port}/tcp"],
            ["sudo", "-n", "ufw", "allow", f"{port}/tcp"],
            ["ufw", "allow", f"{port}/tcp"]
        ]
        for cmd in attempts:
            try:
                res = self.runner(cmd)
                if res.returncode == 0:
                    return True
            except Exception:
                continue
        return False


class FirewalldBackend(NetAccessBackend):
    """RHEL/Fedora/CentOS Firewalld."""

    @property
    def name(self) -> str:
        return "firewalld"

    def available(self) -> bool:
        try:
            res = self.runner(["firewall-cmd", "--state"])
            return res.returncode == 0 and "running" in (res.stdout or "")
        except Exception:
            return False

    def open(self, port: int) -> bool:
        try:
            r1 = self.runner(["firewall-cmd", "--add-port", f"{port}/tcp", "--permanent"])
            r2 = self.runner(["firewall-cmd", "--reload"])
            return r1.returncode == 0 and r2.returncode == 0
        except Exception:
            return False


class IptablesBackend(NetAccessBackend):
    """Direct iptables fallback."""

    @property
    def name(self) -> str:
        return "iptables"

    def available(self) -> bool:
        return True

    def open(self, port: int) -> bool:
        try:
            res = self.runner(["iptables", "-A", "INPUT", "-p", "tcp", "--dport", str(port), "-j", "ACCEPT"])
            return res.returncode == 0
        except Exception:
            return False
