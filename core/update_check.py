"""Is there a newer version of the eagle than the one running?

Only a checkout the installer made is checked (it leaves `.aethelark-install`
in it); a developer's working copy has its own idea of what is current. One
`git ls-remote`, no download.
"""
from __future__ import annotations

import subprocess
from pathlib import Path


def _git(repo: Path, *args: str, timeout: float = 10) -> str:
    out = subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                         text=True, timeout=timeout)
    return out.stdout.strip() if out.returncode == 0 else ""


def newer_version(repo: Path) -> str | None:
    """The short id of a newer commit on the remote, or None."""
    repo = Path(repo)
    if not (repo / ".aethelark-install").exists():
        return None
    try:
        branch = _git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        local = _git(repo, "rev-parse", "HEAD")
        remote = _git(repo, "ls-remote", "origin", f"refs/heads/{branch}").split("\t")[0]
    except (OSError, subprocess.SubprocessError):
        return None
    if not (branch and local and remote) or remote == local:
        return None
    return remote[:7]
