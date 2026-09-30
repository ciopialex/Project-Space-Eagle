"""The socket a sovereign domain module plugs into.

Space-Eagle's own tools are hardcoded: 35 `ToolSpec` entries and 35 matching
`TOOL_DECLARATIONS` dicts in `main.py`, each wired to an import. That is right
for the eagle's own hands — mouse, audio, files, browser — which ship with it
and are not optional.

It is wrong for domain engines. `a3d` slices and prints 3D models; `alaw`
answers Romanian tax questions; neither is installed on most machines and
neither belongs in this repo. Hardcoding them would put dead imports in the
boot path of every user who has neither, and adding a third would mean editing
`main.py` again. So they are discovered instead: a manifest describes what a
module can do, the bus checks whether its binary exists, and only what actually
resolves is offered to the model.

What this deliberately does NOT do
----------------------------------
It does not parse a module's human-readable output into structure. `a3d` prints
Rich tables with box-drawing and emoji; a parser over that is exactly the
brittleness the CLIfication principle exists to remove, one layer down — it
would break silently the first time a column moved and report confident wrong
answers until someone noticed. A module declaring `output = "text"` has its
stdout handed to the model verbatim. Models read tables fine. A module that
wants structure declares `output = "json"` and emits JSON, and if it emits
something else the bus says so rather than inventing a shape.

The three modules named in the ecosystem, measured 2026-08-19: `a3d` resolves
on PATH and has no `--json` flag; `alaw` is declared in Aethelark-Law's
`pyproject.toml` but not installed; `atrade` does not exist at all — that
repo's entry points are `insiders`, `smartmoney`, `scrapetrade` and
`aethelark`. Only `a3d` ships a manifest here. The others get one when they
have a CLI to point it at, and until then the bus reports them as absent
rather than pretending.

Isolation is the load-bearing property
--------------------------------------
Tools run on `_make_tool_executor`'s shared `ThreadPoolExecutor`, alongside the
voice turn loop. A module that blocks forever holds one of those threads. So
every invocation is a separate process with a hard timeout, and every failure
path — missing binary, bad argv, crash, hang, unparseable output — returns a
`ToolResult` rather than raising. `invoke()` has no path that propagates an
exception to its caller.
"""
from __future__ import annotations

from .bundle import BundleError, ModuleManager, build_bundle
from .bus import ModuleBus, default_manifest_dirs, module_bin_dir, which_module
from .manifest import (
    ModuleManifest, ModuleTool, build_argv, load_manifest, load_manifests,
)

__all__ = [
    "BundleError",
    "ModuleBus",
    "ModuleManager",
    "build_bundle",
    "ModuleManifest",
    "ModuleTool",
    "build_argv",
    "default_manifest_dirs",
    "module_bin_dir",
    "which_module",
    "load_manifest",
    "load_manifests",
]
