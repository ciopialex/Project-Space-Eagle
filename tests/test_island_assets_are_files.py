"""The host must register the card's stylesheet, not the name of it.

A seam: the module declares `[island] css = "style.css"` in its manifest, and
the host reads that declaration to decide what to hand the page. It used the
declared value as the stylesheet's CONTENT, so atrade -- the only module that
declares the key -- registered a <style> tag holding the nine characters
"style.css". Zero rules. The card drew with no stylesheet at all: every stage
visible at once, no layout, the logo at its natural size.

The whole suite was green while that shipped, because nothing called the
loader. It lived inside a closure inside `_setup_module_watcher`; splitting
`island_assets` out is what made it reachable, and this is the check.
"""
import pytest

from aethelark_web import island_assets
from core.module_bus import default_manifest_dirs, load_manifests

MANIFESTS = [m for m in load_manifests(default_manifest_dirs())]


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.key)
def test_a_declared_stylesheet_is_loaded_as_css_not_as_its_filename(manifest):
    declared = (getattr(manifest, "island_config", None) or {}).get("css")
    tpl, css = island_assets(manifest)
    if not declared and not css:
        pytest.skip(f"{manifest.key} ships no island stylesheet")
    assert css != declared, (
        f"{manifest.key}: the host registered the literal string {declared!r} "
        f"as its stylesheet. That is the filename, not the file.")
    # A stylesheet has rules. The filename never will.
    assert "{" in css and "}" in css, (
        f"{manifest.key}: registered stylesheet has no CSS rule in it "
        f"({css[:60]!r})")


@pytest.mark.parametrize("manifest", MANIFESTS, ids=lambda m: m.key)
def test_a_module_with_a_card_gets_a_template(manifest):
    tpl, css = island_assets(manifest)
    if manifest.island is None:
        pytest.skip(f"{manifest.key} declares no island face")
    assert tpl.strip(), f"{manifest.key} declares cards but no template loaded"
    assert "{" in tpl, f"{manifest.key}: template has no placeholders"
