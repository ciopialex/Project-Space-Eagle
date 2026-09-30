"""Abstract base class for platform firewall backends and injected runner."""
from __future__ import annotations

import abc
import subprocess
from typing import Any, Callable, List, Optional, Protocol


class CommandRunner(Protocol):
    def __call__(self, args: List[str], *, timeout: Optional[float] = None, **kw: Any) -> Any:
        ...


def default_runner(args: List[str], *, timeout: Optional[float] = 10.0, **kw: Any) -> Any:
    """Default non-shell subprocess runner."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        **kw
    )


class NetAccessBackend(abc.ABC):
    """Abstract firewall controller for an OS subsystem."""

    def __init__(self, runner: Optional[CommandRunner] = None) -> None:
        self.runner: CommandRunner = runner or default_runner

    @property
    @abc.abstractmethod
    def name(self) -> str:
        """The short identifier for this backend (e.g. 'ufw', 'firewalld')."""
        ...

    @abc.abstractmethod
    def available(self) -> bool:
        """Returns True if this firewall backend is active and present on the system."""
        ...

    @abc.abstractmethod
    def open(self, port: int) -> bool:
        """Attempts to open the given TCP port. Returns True on success."""
        ...
