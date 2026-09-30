"""A module borrows a sign-in from the eagle's browser, and only that one.

The seam between the harness and a module that needs a web account: the user
signs in once, in the eagle's own browser; the module declares the account in
its manifest (`[[accounts]]`: site, proof cookie, receive command); the host
hands over that site's cookies on the command's stdin. Before this, a3d's
MakerWorld downloads failed on every install but the developer's, and signing
in through Settings changed nothing, because the module could not see it.

What must hold, because each failure is either a leak or a dead end:
  - only the declared site's session crosses, on stdin, never in argv;
  - nothing crosses when the browser holds no proof of a login;
  - a call refused for want of a sign-in is repaired and retried once --
    unless the tool is permanent, which is never re-run behind the user's back.

The module here is a real executable that records what it was handed.
"""
from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus import accounts  # noqa: E402
from core.module_bus.manifest import load_manifest  # noqa: E402
from core.tool_result import ToolResult  # noqa: E402

SESSION = [{"name": "token", "value": "SECRET-TOKEN", "domain": ".makerworld.com"},
           {"name": "cf_clearance", "value": "cf", "domain": ".makerworld.com"}]


def _module(tmp_path, *, danger="undoable", accounts_toml=None):
    """A manifest plus a real command that writes its argv and stdin to disk."""
    record = tmp_path / "received.json"
    binary = tmp_path / "fakemod"
    binary.write_text(
        "#!%s\nimport json, sys\n"
        "json.dump({'argv': sys.argv[1:], 'stdin': sys.stdin.read()}, open(%r, 'w'))\n"
        "print(json.dumps({'success': True}))\n" % (sys.executable, str(record)))
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    manifest = tmp_path / "manifest.toml"
    manifest.write_text(f'''
key = "fakemod"
binary = "fakemod"
output = "json"
{accounts_toml if accounts_toml is not None else """
[[accounts]]
site = "makerworld.com"
proof = "token"
why = "Downloads need it."
receive = ["session", "--json"]
"""}
[[tools]]
name = "download"
description = "Download one model."
argv = ["download", "{{id}}", "--json"]
danger = "{danger}"
{'confirm_prompt = "Really?"' if danger == "permanent" else ""}
  [tools.params.id]
  type = "STRING"
  description = "Model id."
  required = true
''')
    return load_manifest(manifest), (lambda name: str(binary)), record


def test_only_the_declared_site_crosses_and_only_on_stdin(tmp_path):
    manifest, which, record = _module(tmp_path)
    result = accounts.hand_over(manifest, manifest.accounts[0], SESSION, which)
    assert result["ok"] is True
    got = json.loads(record.read_text())
    assert got["argv"] == ["session", "--json"]
    assert "SECRET-TOKEN" not in " ".join(got["argv"]), (
        "the session went on the command line, where any process can read it")
    handed = json.loads(got["stdin"])
    assert handed["site"] == "makerworld.com"
    assert {c["name"] for c in handed["cookies"]} == {"token", "cf_clearance"}


def test_a_browser_that_is_not_signed_in_hands_nothing_over(tmp_path):
    manifest, which, record = _module(tmp_path)
    result = accounts.hand_over(manifest, manifest.accounts[0],
                                [{"name": "cf_clearance", "value": "cf"}], which)
    assert result == {"ok": False, "signed_in": False,
                      "detail": "the eagle's browser is not signed in to makerworld.com"}
    assert not record.exists(), "the module was run with a signed-out session"


def test_a_sign_in_reaches_the_modules_that_declared_that_site(tmp_path):
    manifest, which, record = _module(tmp_path)
    asked = []

    def cookies(site, start=False):
        asked.append(site)
        return SESSION

    done = accounts.after_sign_in("https://www.makerworld.com/en/models", [manifest],
                                  which, cookies_fn=cookies, log=lambda _l: None)
    assert [(k, s, r["ok"]) for k, s, r in done] == [("fakemod", "makerworld.com", True)]
    assert asked == ["makerworld.com"]
    assert accounts.after_sign_in("https://youtube.com", [manifest], which,
                                  cookies_fn=cookies, log=lambda _l: None) == []


@pytest.mark.parametrize("bad, why", [
    ('site = "https://makerworld.com/"\nproof = "token"\nreceive = ["session"]', "bare domain"),
    ('site = "makerworld.com"\nreceive = ["session"]', "no proof"),
    ('site = "makerworld.com"\nproof = "token"', "no receive"),
])
def test_an_account_the_host_could_not_hand_over_does_not_load(tmp_path, bad, why):
    with pytest.raises(ValueError, match=why):
        _module(tmp_path, accounts_toml="[[accounts]]\n" + bad)


class _Bus:
    """The bus as main.py's _invoke_module sees it: a first call refused for
    want of a sign-in, then whatever the retry returns."""

    def __init__(self, manifest):
        self.manifest, self.calls = manifest, 0

    def invoke(self, name, args, timeout_s=0):
        self.calls += 1
        if self.calls == 1:
            return ToolResult.failure("fakemod download failed: log in first",
                                      guidance="x", needs_account="makerworld.com")
        return ToolResult.success("downloaded")

    def manifest_of(self, name):
        return self.manifest

    def which(self, binary):
        return "/bin/true"


@pytest.mark.parametrize("danger, retried", [("undoable", True), ("permanent", False)])
def test_a_refused_call_is_repaired_and_retried_unless_it_is_permanent(
        tmp_path, monkeypatch, danger, retried):
    import main
    manifest, _which, _record = _module(tmp_path, danger=danger)
    bus = _Bus(manifest)
    monkeypatch.setattr(main, "MODULE_BUS", bus)
    monkeypatch.setattr(accounts, "repair",
                        lambda m, site, which, *a, **k: {"ok": True, "signed_in": True,
                                                         "detail": "handed over"})
    live = main.AethelarkLive.__new__(main.AethelarkLive)
    result = live._invoke_module("fakemod_download", {"id": "1"}, 5.0)
    assert (bus.calls == 2) is retried
    assert result.ok is retried
    if not retried:
        assert "Call this again now" in result.guidance


def test_with_no_sign_in_anywhere_the_model_is_told_how_to_get_one(tmp_path, monkeypatch):
    import main
    manifest, _which, _record = _module(tmp_path)
    bus = _Bus(manifest)
    monkeypatch.setattr(main, "MODULE_BUS", bus)
    monkeypatch.setattr(accounts, "repair",
                        lambda *a, **k: {"ok": False, "signed_in": False,
                                         "detail": "not signed in"})
    live = main.AethelarkLive.__new__(main.AethelarkLive)
    result = live._invoke_module("fakemod_download", {"id": "1"}, 5.0)
    assert bus.calls == 1 and not result.ok
    assert "action='sign_in'" in result.guidance
    assert "https://makerworld.com" in result.guidance
