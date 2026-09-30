"""How much of the new code is the old code, measured three ways, not one.

A single "tokens minus comments and strings, compare 8-grams" number sounds
rigorous and is not. A copy of actions/proactive.py with fifteen identifiers
renamed and not one line of logic changed scores 11% containment on exactly
that metric, because a renamed NAME token is still a NAME token holding a
different string, and that is enough to break every 8-gram shingle it falls
inside. A tool that reports one number here would call a disguised copy
"independent work" — which is the one failure this tool exists to prevent.

So this reports three registers, and none of them alone is the verdict:

  literal      tokens verbatim, only STRING content blurred to "STR" (the
               original behaviour). Catches copy-paste: identical code,
               identical names.
  structural   additionally blurs every NAME to "N" — unless it is a
               language keyword, since collapsing "if" and "while" into the
               same token would erase the one thing this register exists to
               keep — and every NUMBER to "#". Rename-invariant by
               construction: the fifteen-renamed-identifiers copy above
               scores ~100% here, because the token shape under the renames
               never changed.
  strings      the *set* of string literals of real length (>=12 chars,
               whitespace-collapsed), compared as a set rather than
               shingled. literal and structural both normalise string
               CONTENT away on purpose — that is what makes them
               rename-and-reword invariant — which means a docstring or
               prompt template copied verbatim is invisible to both. This
               register exists to catch that specific copy.

Jaccard is symmetric and forgiving of size differences; containment answers
the question that actually matters — what fraction of the NEW file already
existed in the old one. Neither means anything unanchored: run --control to
see where genuinely unrelated files in this repo sit on all three before
trusting what a "similar" number implies about a real pair.
"""
from __future__ import annotations

import argparse
import ast
import io
import keyword
import re
import statistics
import tokenize
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
K = 8
MIN_STRING = 12  # chars, after whitespace-collapsing, to count in the strings set

_KEYWORDS = frozenset(keyword.kwlist)

# tokenize splits an f-string into FSTRING_START/MIDDLE/END rather than one
# STRING token from Python 3.12 onward; on older interpreters these type
# names do not exist at all. Built once so every string-shaped token — plain
# or f-string — gets the same STR/S treatment in _python_tokens below,
# instead of f-strings silently falling through to the verbatim branch.
_STRING_TYPES = {tokenize.STRING}
for _name in ("FSTRING_START", "FSTRING_MIDDLE", "FSTRING_END"):
    if hasattr(tokenize, _name):
        _STRING_TYPES.add(getattr(tokenize, _name))

_SKIP_TYPES = (tokenize.COMMENT, tokenize.NL, tokenize.NEWLINE,
               tokenize.INDENT, tokenize.DEDENT, tokenize.ENDMARKER)

# Pairs already in this repo with no plausible shared ancestry — different
# subsystems, nothing copied between them — used by --control to show where
# "unrelated" actually sits on all three metrics before any real pair is
# judged against it.
CONTROL_PAIRS = [
    ("core/quota.py", "memory/config_manager.py"),
    ("core/vision_guard.py", "actions/weather_report.py"),
    ("core/trace.py", "actions/repo_map.py"),
    ("core/user_paths.py", "actions/autostart.py"),
    ("memory/memory_manager.py", "actions/screen_processor.py"),
]

# A conservative set of ECMAScript reserved/contextual words — the "keyword"
# carve-out for _text_tokens' structural register, playing the role
# keyword.kwlist plays for Python. Deliberately not HTML tag names or CSS
# properties: those vary file to file on their own and are not "structure"
# in the sense this register means, and normalising too much rather than
# too little is the safer failure mode for a tool whose job is to not
# under-report overlap.
_TEXT_KEYWORDS = frozenset({
    "break", "case", "catch", "class", "const", "continue", "debugger",
    "default", "delete", "do", "else", "export", "extends", "finally",
    "for", "function", "if", "import", "in", "instanceof", "new", "return",
    "super", "switch", "this", "throw", "try", "typeof", "var", "void",
    "while", "with", "yield", "let", "static", "enum", "await", "async",
    "of", "get", "set", "null", "true", "false", "undefined",
})


@dataclass(frozen=True)
class Metric:
    """One register's comparison of OLD against NEW.

    jaccard and containment are None — N/A, not a number — whenever either
    side's set was empty. See _ratio for why that has to be a hard rule
    rather than a special-cased 1.0 or 0.0.
    """

    old_n: int
    new_n: int
    shared: int
    jaccard: float | None
    containment: float | None  # of NEW, also in OLD


def _ratio(a: set, b: set) -> Metric:
    """Jaccard and containment for two sets, plus both raw counts.

    An empty set on either side is not a measurement. A prior version of
    this function reported containment=1.0 whenever NEW was empty (nothing
    in NEW, so vacuously "all of it" is in OLD) while still reporting
    Jaccard=0.0 for that same pair (empty and non-empty share nothing) --
    two contradictory answers for one input, caught by review. And it is
    reachable on real input: any rewritten file with no string literal
    >=MIN_STRING chars empties the strings set, any file under K tokens
    empties a shingle set. Against a populated OLD file that printed
    containment 100% -- "all of this file came from the old one" -- for a
    file that had nothing to compare at all. Exactly backwards.

    Neither 1.0 nor 0.0 is honest here, in either direction, on either
    side: a ratio with an empty denominator is not "total overlap" or "no
    overlap", it is undefined, the same way 0/0 is. So the rule is simply
    that either set being empty makes the whole comparison N/A -- both
    metrics, together, every time. That only changes what happens when a
    set is empty; every other comparison is still the plain ratio.
    """
    if not a or not b:
        return Metric(len(a), len(b), len(a & b), None, None)
    inter = len(a & b)
    union = len(a | b)
    return Metric(len(a), len(b), inter, inter / union, inter / len(b))


def _python_tokens(src: str, *, structural: bool) -> list[str]:
    """Token stream for one Python file, in either register.

    literal keeps every NAME, NUMBER and OP exactly as written and only
    blurs string content. structural additionally blurs every NAME to "N"
    (keywords excepted) and every NUMBER to "#", so a file whose logic is
    untouched but whose identifiers were all renamed lines up token-for-
    token with the original it was renamed from.
    """
    out: list[str] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in _SKIP_TYPES:
                continue
            if tok.type in _STRING_TYPES:
                out.append("S" if structural else "STR")
            elif structural and tok.type == tokenize.NUMBER:
                out.append("#")
            elif (structural and tok.type == tokenize.NAME
                  and tok.string not in _KEYWORDS):
                out.append("N")
            else:
                out.append(tok.string)
    except tokenize.TokenError:
        pass
    return out


def _literal_string_value(tok_string: str) -> str:
    """The decoded content of one Python STRING token's source text, or ""
    if it is not something Python can evaluate offline (an f-string with an
    interpolation, on an interpreter old enough to tokenize it as STRING)."""
    try:
        value = ast.literal_eval(tok_string)
    except Exception:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    return value if isinstance(value, str) else ""


def _python_strings(src: str) -> set[str]:
    """Every string literal in the file, whitespace-collapsed, >=MIN_STRING
    chars, as a set of contents.

    Independent of the token stream above on purpose: a docstring or prompt
    template pasted verbatim is one "S" token to both registers there, same
    as any other string of any other length — comparing the strings
    themselves is the only way to catch that copy.
    """
    found: set[str] = set()
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.STRING:
                content = _literal_string_value(tok.string)
            elif (hasattr(tokenize, "FSTRING_MIDDLE")
                  and tok.type == tokenize.FSTRING_MIDDLE):
                content = tok.string  # already-decoded literal text, no quotes to strip
            else:
                continue
            collapsed = " ".join(content.split())
            if len(collapsed) >= MIN_STRING:
                found.add(collapsed)
    except tokenize.TokenError:
        pass
    return found


def _text_tokens(src: str, *, structural: bool) -> list[str]:
    """HTML/JS/CSS: identifiers, numbers and punctuation, comments stripped.

    structural blurs identifiers to "N" and digit runs to "#" — the same
    rename-invariance _python_tokens gets from normalising NAME — while
    leaving punctuation and a fixed list of language keywords literal, so
    "for(" and "if(" do not blur into the same token as everything else.
    """
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", " ", src)
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    raw = re.findall(r"[A-Za-z_][A-Za-z0-9_-]*|\d+|[^\s\w]", src)
    if not structural:
        return raw
    out: list[str] = []
    for t in raw:
        if t[0].isdigit():
            out.append("#")
        elif t[0].isalpha() or t[0] == "_":
            out.append(t if t in _TEXT_KEYWORDS else "N")
        else:
            out.append(t)
    return out


_QUOTED = re.compile(
    r'"((?:[^"\\]|\\.)*)"|\'((?:[^\'\\]|\\.)*)\'|`((?:[^`\\]|\\.)*)`', re.S)


def _text_strings(src: str) -> set[str]:
    """Every quoted literal in HTML/JS/CSS source, content only, >=MIN_STRING
    chars — the same "compare the content itself" register _python_strings
    provides for Python, so every pair gets all three metrics regardless of
    file type."""
    src = re.sub(r"/\*.*?\*/", " ", src, flags=re.S)
    src = re.sub(r"(?m)//.*$", " ", src)
    src = re.sub(r"<!--.*?-->", " ", src, flags=re.S)
    found: set[str] = set()
    for m in _QUOTED.finditer(src):
        content = next(g for g in m.groups() if g is not None)
        collapsed = " ".join(content.split())
        if len(collapsed) >= MIN_STRING:
            found.add(collapsed)
    return found


def tokens_for(path: Path, *, structural: bool) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    if path.suffix == ".py":
        return _python_tokens(src, structural=structural)
    return _text_tokens(src, structural=structural)


def strings_for(path: Path) -> set[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    return _python_strings(src) if path.suffix == ".py" else _text_strings(src)


def shingles(toks: list[str], k: int = K) -> set[tuple[str, ...]]:
    return {tuple(toks[i:i + k]) for i in range(max(0, len(toks) - k + 1))}


def compare(old: Path, new: Path) -> dict[str, Metric]:
    """All three registers for one pair. This is the whole tool — main() and
    --control are both just ways of printing what this returns."""
    return {
        "literal": _ratio(shingles(tokens_for(old, structural=False)),
                           shingles(tokens_for(new, structural=False))),
        "structural": _ratio(shingles(tokens_for(old, structural=True)),
                              shingles(tokens_for(new, structural=True))),
        "strings": _ratio(strings_for(old), strings_for(new)),
    }


def _fmt_ratio(x: float | None) -> str:
    return "N/A" if x is None else f"{x:.4f}"


def _fmt_pct(x: float | None) -> str:
    return "N/A" if x is None else f"{x:.4%}"


def _print_metric(name: str, m: Metric) -> None:
    print(f"  [{name}]")
    print(f"    old                 : {m.old_n}")
    print(f"    new                 : {m.new_n}")
    print(f"    shared              : {m.shared}")
    print(f"    Jaccard             : {_fmt_ratio(m.jaccard)}")
    print(f"    of NEW, also in OLD : {_fmt_pct(m.containment)}")


def report_pair(old: Path, new: Path, *, verbose: bool = False) -> None:
    result = compare(old, new)
    print(f"{old.name} -> {new.name}")
    for name in ("literal", "structural", "strings"):
        _print_metric(name, result[name])
    if verbose:
        a = shingles(tokens_for(old, structural=False))
        b = shingles(tokens_for(new, structural=False))
        shared = sorted(a & b)[:20]
        if shared:
            print("  sample shared literal shingles:")
            for s in shared:
                print("    shared:", " ".join(s))


def _run_control() -> int:
    print("[overlap] --control: noise floor from unrelated pairs already in this repo\n")
    jacs: dict[str, list[float]] = {"literal": [], "structural": [], "strings": []}
    conts: dict[str, list[float]] = {"literal": [], "structural": [], "strings": []}
    ran = 0
    for old_rel, new_rel in CONTROL_PAIRS:
        old, new = ROOT / old_rel, ROOT / new_rel
        if not (old.exists() and new.exists()):
            print(f"[overlap] skipping {old_rel} <-> {new_rel}: file not found")
            continue
        ran += 1
        result = compare(old, new)
        print(f"{old_rel}  <->  {new_rel}")
        for name in ("literal", "structural", "strings"):
            m = result[name]
            print(f"  {name:10} Jaccard {_fmt_ratio(m.jaccard)}    containment {_fmt_pct(m.containment)}")
            # N/A entries (an empty set on one side) are excluded, not
            # coerced to 0.0 or 1.0 -- a median that silently absorbed N/A
            # as a number would just relocate the F1 bug into --control.
            if m.jaccard is not None:
                jacs[name].append(m.jaccard)
            if m.containment is not None:
                conts[name].append(m.containment)
        print()
    if not ran:
        print("[overlap] no control pairs were found on disk — nothing to report")
        return 1
    print(f"median across {ran} unrelated pair(s) — the noise floor:")
    for name in ("literal", "structural", "strings"):
        # jaccard and containment are always N/A together (see _ratio), so
        # one count describes both.
        n = len(jacs[name])
        if n:
            print(f"  {name:10} Jaccard {statistics.median(jacs[name]):.4f}"
                  f"    containment {statistics.median(conts[name]):.4%}"
                  f"    (n={n}/{ran})")
        else:
            print(f"  {name:10} N/A — every pair had an empty set on this register (n=0/{ran})")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Token-overlap receipts for two files, or a repo noise floor.")
    ap.add_argument("old", nargs="?", help="the earlier file")
    ap.add_argument("new", nargs="?", help="the later file")
    ap.add_argument("-v", "--verbose", action="store_true",
                     help="print a sample of shared literal shingles")
    ap.add_argument("--control", action="store_true",
                     help="run the three metrics over unrelated repo pairs and print the noise floor")
    args = ap.parse_args(argv)

    if args.control:
        return _run_control()
    if not args.old or not args.new:
        ap.error("OLD and NEW are required unless --control is given")
    report_pair(Path(args.old), Path(args.new), verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
