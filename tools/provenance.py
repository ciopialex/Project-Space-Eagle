from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_HEADER = re.compile(r"^[0-9a-f]{40} \d+ \d+")


def _is_binary(path: str, cwd: Path | None = None) -> bool:
    result = subprocess.run(
        ["git", "ls-files", "--eol", "--", path],
        cwd=cwd or ROOT, capture_output=True, text=True)
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 1 and parts[0] == "i/-text":
            return True
    return False


def lines_by(path: str, author: str = "FatihMakes", rev: str = "HEAD",
             cwd: Path | None = None) -> int:
    if _is_binary(path, cwd):
        return 0

    result = subprocess.run(
        ["git", "blame", "--line-porcelain", "-w", "-M", "-C", rev, "--", path],
        cwd=cwd or ROOT, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.decode('utf-8', errors='replace').strip())
    out = result.stdout.decode('utf-8', errors='replace')
    count, current = 0, ""
    for line in out.splitlines():
        if _HEADER.match(line):
            current = ""
        elif line.startswith("author "):
            current = line[7:]
        elif line.startswith("\t") and current == author and line[1:].strip():
            count += 1
    return count


def _tracked() -> list[str]:
    result = subprocess.run(["git", "ls-files"], cwd=ROOT, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip())
    return [p for p in result.stdout.split("\n") if p and not p.startswith("tests/fixtures/")]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--author", default="FatihMakes")
    ap.add_argument("--fail-on", nargs="*", default=[])
    args = ap.parse_args(argv)
    total, failing, errors = 0, [], False
    for path in _tracked():
        try:
            n = lines_by(path, args.author)
        except RuntimeError as e:
            if path in args.fail_on:
                print(f"ERROR {path} cannot be blamed: {e}", file=sys.stderr)
                return 2
            print(f"ERROR {path}: {e}", file=sys.stderr)
            errors = True
            n = 0
        if n:
            print(f"{n:6d} {path}")
            total += n
        if path in args.fail_on and n:
            failing.append(path)
    print(f"{total:6d} total")
    for path in failing:
        print(f"FAIL {path} still carries {args.author} lines", file=sys.stderr)
    if errors:
        return 2
    return 1 if failing else 0


if __name__ == "__main__":
    sys.exit(main())
