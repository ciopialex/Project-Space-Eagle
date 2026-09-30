"""What a module's deeper card needs is the module's to declare.

The host carried `{"atrade": ("atrade_analyze", "atrade_governance")}` in a
table of its own, and found a tool's module by cutting its name at the first
underscore -- a different module for any key with an underscore in it. Both
come from the manifest now: `prefetch` on a card, and the bus's own index.
"""
import pytest

from core.module_bus import ModuleBus
from core.module_bus.manifest import load_manifest

MANIFEST = '''
key = "{key}"
binary = "true"
output = "json"

[[tools]]
name = "quick"
description = "d"
argv = ["quick", "--json"]

[[tools]]
name = "deep"
description = "d"
argv = ["deep", "--json"]

[island]
about = "id"
first = "small"

  [island.cards.small]
  size = {{ w = 320, h = 62 }}
  shows = ["id", "value"]
  prefetch = {prefetch}

  [island.cards.large]
  size = {{ w = 440, h = 364 }}
'''


def _write(tmp_path, key, prefetch):
    (tmp_path / "island").mkdir(exist_ok=True)
    (tmp_path / "island" / "template.html").write_text("<div>{id} {value}</div>")
    (tmp_path / f"{key}.toml").write_text(MANIFEST.format(key=key, prefetch=prefetch))


def _bus(tmp_path, key="my_mod", prefetch='["deep", "quick"]'):
    _write(tmp_path, key, prefetch)
    return ModuleBus(manifest_dirs=[tmp_path], which=lambda n: "/bin/true").discover()


def test_depth_tools_come_from_the_manifest_in_order(tmp_path):
    assert _bus(tmp_path).depth_tools("my_mod") == ("my_mod_deep", "my_mod_quick")


def test_a_key_with_an_underscore_is_its_own_module(tmp_path):
    bus = _bus(tmp_path)
    assert bus.module_of("my_mod_deep") == "my_mod"      # not "my"


def test_a_module_that_declares_nothing_has_no_depth(tmp_path):
    assert _bus(tmp_path).depth_tools("someone_else") == ()


def test_prefetching_a_tool_the_module_lacks_refuses_the_manifest(tmp_path):
    _write(tmp_path, "m", '"missing"')
    with pytest.raises(ValueError, match="prefetch 'missing' is not a tool"):
        load_manifest(tmp_path / "m.toml")
