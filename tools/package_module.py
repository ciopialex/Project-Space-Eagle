#!/usr/bin/env python3
"""Build a `.aem` bundle from an installed module directory.

    tools/package_module.py ~/.aethelark/modules/atrade \
        --package aethelark_trade --out dist/atrade.aem

The `--package` is the module's Python backend, resolved by import name and
vendored into the archive. Without it the bundle is metadata and island assets
only — which is what every bundle shipped so far has been, and why a buyer's
download resolved to a directory on somebody else's laptop.

Vendoring the source does not make a module self-contained: `atrade` imports
numpy, polars and yfinance, and two of those are compiled wheels, so a bundle
that carries source still needs its dependencies present on the target machine
and is per-platform once they are vendored. That is a distribution decision,
not a packaging one; this tool reports what it shipped so the gap is visible
rather than assumed away.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import importlib.util
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.module_bus.bundle import BundleError, build_bundle  # noqa: E402
from core.module_bus.manifest import load_manifest  # noqa: E402


def resolve_package(name: str) -> Path:
    """The on-disk source directory for an importable package."""
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.origin:
        raise BundleError(f"cannot import {name!r} — is it installed?")
    directory = Path(spec.origin).parent
    if not directory.is_dir():
        raise BundleError(f"{name!r} is not a package directory")
    return directory


def read_requirements(source: Path) -> list[str]:
    """The dependency list from a pyproject or a requirements file.

    Recorded into the manifest so the installer knows what to provision. A
    bundle carrying source rather than a packed interpreter has to say what it
    needs, or the environment built for it is empty.
    """
    if source.name == "pyproject.toml":
        import tomllib
        data = tomllib.loads(source.read_text(encoding="utf-8"))
        return list(data.get("project", {}).get("dependencies", []) or [])
    lines = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            lines.append(line)
    return lines


def declare_requirements(module_dir: Path, requirements: list[str]) -> list[str]:
    """Write the vetted requirement list into the module's manifest."""
    from core.module_bus.bundle import vet_requirement

    vetted = [vet_requirement(r) for r in requirements]
    manifest = module_dir / "manifest.toml"
    text = manifest.read_text(encoding="utf-8")
    rendered = "requirements = [" + ", ".join(
        json.dumps(r) for r in vetted) + "]"

    lines = [ln for ln in text.splitlines()
             if not ln.strip().startswith("requirements =")]
    # Above the first [[tools]] block so it stays in the top-level table.
    for index, line in enumerate(lines):
        if line.strip().startswith("[["):
            lines.insert(index, rendered)
            lines.insert(index + 1, "")
            break
    else:
        lines.append(rendered)
    manifest.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return vetted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module_dir", type=Path,
                        help="the module directory (holding manifest.toml)")
    parser.add_argument("--package", default=None,
                        help="import name of the Python backend to vendor")
    parser.add_argument("--package-path", type=Path, default=None,
                        help="path to the backend, instead of an import name")
    parser.add_argument("--out", type=Path, default=None,
                        help="output .aem (default: <key>.aem beside the module)")
    parser.add_argument("--sign", type=Path, default=None,
                        help="Ed25519 private key to sign the bundle with")
    parser.add_argument("--requirements-from", type=Path, default=None,
                        help="pyproject.toml or requirements.txt whose "
                             "dependencies the bundle should declare")
    args = parser.parse_args(argv)

    try:
        manifest = load_manifest(args.module_dir / "manifest.toml")
    except (ValueError, OSError) as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    package = args.package_path
    if package is None and args.package:
        try:
            package = resolve_package(args.package)
        except BundleError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

    declared: list[str] = list(manifest.requirements)
    if args.requirements_from:
        try:
            declared = declare_requirements(
                args.module_dir, read_requirements(args.requirements_from))
        except BundleError as e:
            print(f"✗ {e}", file=sys.stderr)
            return 1

    out = args.out or (args.module_dir.parent / f"{manifest.key}.aem")
    try:
        bundle = build_bundle(args.module_dir, out, package=package)
    except BundleError as e:
        print(f"✗ {e}", file=sys.stderr)
        return 1

    if args.sign:
        from core.module_bus.signing import sign_bundle
        try:
            sign_bundle(bundle, args.sign)
        except (ValueError, OSError) as e:
            print(f"✗ could not sign: {e}", file=sys.stderr)
            return 1

    digest = hashlib.sha256(bundle.read_bytes()).hexdigest()
    bundle.with_suffix(".aem.sha256").write_text(f"{digest}  {bundle.name}\n")

    with zipfile.ZipFile(bundle) as archive:
        names = archive.namelist()
    backend = sorted({n.split("/", 1)[0] for n in names
                      if "/" in n and not n.startswith("island/")
                      and not n.startswith("bin/")})

    print(f"✔ {manifest.key}: {len(manifest.tools)} tools")
    print(f"  → {bundle}  ({bundle.stat().st_size:,} bytes, {len(names)} entries)")
    print(f"  sha256 {digest}")
    if args.sign:
        from core.module_bus.bundle import ROOT_PUBLIC_KEY
        from core.module_bus.signing import verify_bundle
        good = verify_bundle(bundle, ROOT_PUBLIC_KEY)
        print(f"  signed: {'verifies against the shipped root key' if good else
                           'SIGNED WITH A KEY THIS HARNESS DOES NOT TRUST'}")
    else:
        print("  ⚠ unsigned — install will refuse it unless allow_unsigned=True")
    if declared:
        print(f"  requirements declared: {len(declared)} "
              f"(provisioned into the module's own .venv on install)")
    if backend:
        print(f"  backend vendored: {', '.join(backend)}")
    else:
        print("  ⚠ no backend vendored — this bundle carries no runnable code. "
              "Pass --package to include it.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
