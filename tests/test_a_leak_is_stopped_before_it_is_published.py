"""The commit-time guard: a secret cannot be unpublished, so it is refused first."""
import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import secret_scan as guard  # noqa: E402


FAKE_KEY = "sk-" + "q" * 30      # built at run time: the source holds no key-shaped literal


def _diff(path: str, line: str) -> str:
    return f"+++ b/{path}\n@@ -0,0 +1 @@\n+{line}\n"


def test_a_value_from_the_users_own_credentials_is_refused(tmp_path):
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "api_keys.json").write_text(
        json.dumps({"gemini_api_key": FAKE_KEY}))
    values = guard.machine_values(tmp_path)
    found = guard.scan(_diff("notes.py", f'KEY = "{FAKE_KEY}"'),
                       values, ["notes.py"])
    assert any("api_keys.json" in p for p in found)


def test_the_shape_of_a_secret_is_refused_without_knowing_the_value():
    found = guard.scan(_diff("a.py", 'x = "AIza' + "B" * 35 + '"'), {}, ["a.py"])
    assert any("Google API key" in p for p in found)


def test_a_private_file_is_refused_by_its_name():
    assert guard.scan("", {}, ["config/api_keys.json", "memory/long_term.json"])


def test_ordinary_code_and_the_example_env_pass():
    assert guard.scan(_diff("a.py", "def add(a, b): return a + b"), {}, ["a.py", ".env.example"]) == []


def test_the_installed_hook_blocks_a_real_commit(tmp_path):
    subprocess.run(["git", "init", "-q", "-b", "main", str(tmp_path)], check=True)
    hooks = tmp_path / ".githooks"
    hooks.mkdir()
    (hooks / "pre-commit").write_text(
        f'#!/bin/sh\nexec "{sys.executable}" "{REPO}/tools/secret_scan.py" --staged\n')
    (hooks / "pre-commit").chmod(0o755)
    run = lambda *a: subprocess.run(["git", "-C", str(tmp_path), *a], capture_output=True, text=True)
    run("config", "core.hooksPath", ".githooks")
    (tmp_path / "leak.py").write_text('k = "AIza' + "C" * 35 + '"\n')
    run("add", "leak.py")
    result = run("-c", "user.name=t", "-c", "user.email=t@example.org", "commit", "-m", "x")
    assert result.returncode != 0 and "Blocked" in result.stderr


def test_a_commit_under_the_private_address_is_refused_and_noreply_is_not():
    private = {"me@gmail.example"}
    assert guard.identity_problems(["me@gmail.example"], private)
    assert not guard.identity_problems(["12345+me@users.noreply.github.com", "a@b.org"], private)


def test_noreply_is_never_treated_as_private(monkeypatch):
    monkeypatch.setattr(guard.subprocess, "run", lambda *a, **k: type("R", (), {
        "stdout": "1+me@users.noreply.github.com\nreal@gmail.example\n"})())
    assert guard.private_emails() == {"real@gmail.example"}
