"""Guards against publishing the user's private life.

On 2026-07-30 memory/long_term.bak was pushed to a public repo with the
user's language, hobbies, and the names of their mother, a friend and their
partner in it. install.sh git-clones into ~/.aethelark, so every install is a
git checkout with that data inside it - this was never only the maintainer's
problem.

These tests encode Amendment I of the Constitution in core/prompt.txt as
something a machine checks, because prose in a system prompt did not stop it.
"""
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent


def _git(*args):
    return subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True
    )


def _tracked():
    out = _git("ls-files").stdout
    return set(out.splitlines())


# Paths the application writes at runtime. Each is the user's data, living
# inside a git checkout, and each must be unreachable by `git add`.
RUNTIME_DATA = [
    "memory/long_term.json",
    "memory/long_term.bak",       # the file that actually leaked
    "memory/long_term.json.tmp",  # written during every atomic save
    "uploads/whatever-they-dropped.png",
    "config/api_keys.json",
    "config/google_token.json",
    "config/certs/aethelark.key",
    "config/certs/aethelark.crt",
]


@pytest.mark.parametrize("path", RUNTIME_DATA)
def test_runtime_data_is_ignored(path):
    """--no-index matters: a tracked file is reported as not-ignored, which is
    exactly the state that let the service-account key hide for a whole repo
    lifetime."""
    result = _git("check-ignore", "--no-index", "-q", path)
    assert result.returncode == 0, (
        f"{path} is NOT ignored. The app writes it at runtime, so one "
        f"`git add -A` publishes the user's private data."
    )


@pytest.mark.parametrize("path", RUNTIME_DATA)
def test_runtime_data_is_not_tracked(path):
    assert path not in _tracked(), (
        f"{path} is tracked. .gitignore does not apply to files already in "
        f"the index - it needs `git rm --cached {path}`."
    )


def test_memory_module_code_is_still_tracked():
    """The deny-all rule must not swallow the package itself."""
    tracked = _tracked()
    for code in ("memory/__init__.py", "memory/memory_manager.py",
                 "memory/config_manager.py"):
        assert code in tracked, f"{code} stopped being tracked"


def test_no_gitignore_rule_carries_a_trailing_comment():
    """'#' only opens a comment at the START of a line.

    `analytics-credentials.json # note` is one pattern containing spaces and a
    hash, so it matches nothing. A rule written that way silently protects
    nothing at all.
    """
    offenders = []
    for n, raw in enumerate((REPO / ".gitignore").read_text().splitlines(), 1):
        line = raw.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if "#" in line:
            offenders.append(f"  line {n}: {line}")
    assert not offenders, (
        "these .gitignore rules have inline comments and match nothing:\n"
        + "\n".join(offenders)
    )


def test_no_private_key_material_is_tracked():
    for path in _tracked():
        if path.endswith((".key", ".pem", ".p12", ".pfx")):
            pytest.fail(f"private key material is tracked: {path}")


def test_memory_is_written_inside_the_checkout_so_it_must_stay_ignored():
    """Pins the reason the rules above exist.

    If memory ever moves to a proper user-data directory this test should be
    deleted deliberately - which is the point. Right now BASE_DIR is the
    repository root, and that is why memory/ must be ignored wholesale.
    """
    src = (REPO / "memory" / "memory_manager.py").read_text()
    assert 'BASE_DIR / "memory"' in src, (
        "memory_manager no longer writes under BASE_DIR - re-check whether "
        "the ignore rules here still describe reality"
    )
