"""The window's Get button installs catalog modules only.

`eagle install` in a terminal accepts a path or a git URL. The window's bridge
is callable from page script, so a URL arriving there must not reach pip.
"""
from __future__ import annotations

from pathlib import Path

from core.module_bus import installer

REPO = Path(__file__).resolve().parent.parent


def test_catalog_names_and_aliases_are_accepted():
    for name, entry in installer.catalog().items():
        assert installer.catalog_name(name) == name
        for alias in entry.get("aliases", []):
            assert installer.catalog_name(alias.upper()) == name


def test_urls_and_paths_are_not_catalog_modules(tmp_path):
    for spec in ("git+https://example.invalid/evil", "https://example.invalid/x.git",
                 str(tmp_path), "../..", "", "   "):
        assert installer.catalog_name(spec) is None


def test_the_shop_bridge_goes_through_the_catalog():
    source = (REPO / "aethelark_web.py").read_text(encoding="utf-8")
    start = source.index("    def install_module(self, name: str) -> None:")
    body = source[start:source.index("    def _install_job", start)]
    assert "catalog_name(" in body
