from __future__ import annotations

import re
import unicodedata
from collections import Counter
from dataclasses import dataclass
from difflib import SequenceMatcher

_FOLD = str.maketrans({
    "ș": "s", "ş": "s", "ț": "t", "ţ": "t",
    "Ș": "s", "Ş": "s", "Ț": "t", "Ţ": "t",
    "ł": "l", "Ł": "l",
    "ø": "o", "Ø": "o",
    "đ": "d", "Đ": "d",
    "ß": "ss", "ẞ": "ss",
})
_NICKNAMES = {
    "mom": {"mama", "mami", "mum", "mother", "mamica"},
    "dad": {"tata", "tati", "papa", "father"},
    "grandma": {"bunica", "buni", "nana", "granny"},
    "grandpa": {"bunicul", "bunu", "grandad"},
    "wife": {"sotia"},
    "husband": {"sotul"},
    "brother": {"frate", "fratele"},
    "sister": {"sora"},
}
_CLOSE = 0.82
_LEAD = 0.1
_NEAR = 0.6


def normalise(name: str) -> str:
    text = unicodedata.normalize("NFKD", (name or "").translate(_FOLD))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = re.sub(r"[^\w\s]", " ", text.casefold(), flags=re.UNICODE)
    return " ".join(text.replace("_", " ").split())


@dataclass(frozen=True)
class Match:
    kind: str
    title: str | None = None
    candidates: tuple[str, ...] = ()

    @property
    def certain(self) -> bool:
        return self.kind in ("exact", "alias", "sole")


def _nick_words(word: str) -> set[str]:
    out = {word}
    for key, values in _NICKNAMES.items():
        if word == key or word in values:
            out |= {key} | values
    return out


def _word_exact_covers(spoken: list[str], title: list[str]) -> bool:
    need = Counter(spoken)
    have = Counter(title)
    return all(have[w] >= c for w, c in need.items())


def _word_prefix_covers(spoken: list[str], title: list[str]) -> bool:
    return all(any(t == w or (len(w) >= 3 and t.startswith(w)) for t in title) for w in spoken)


def _score(spoken: str, title: str) -> float:
    words = title.split()
    best = max([SequenceMatcher(None, spoken, title).ratio()]
               + [SequenceMatcher(None, spoken, w).ratio() for w in words])
    nick = _nick_words(spoken) if " " not in spoken else set()
    if nick & set(words) - {spoken}:
        best = max(best, 0.9)
    return best


def _duplicate_match(title: str, titles: list[str]) -> Match | None:
    target = normalise(title)
    matches = [t for t in titles if normalise(t) == target]
    if len(matches) <= 1:
        return None
    distinct = list(dict.fromkeys(matches))
    candidates = matches if len(distinct) == 1 else distinct
    return Match("ambiguous", None, tuple(candidates[:3]))


def _finalize(kind: str, title: str, titles: list[str]) -> Match:
    dup = _duplicate_match(title, titles)
    if dup is not None:
        return dup
    return Match(kind, title, (title,))


def match(spoken: str, titles: list[str], aliases: dict[str, str] | None = None) -> Match:
    want = normalise(spoken)
    if not want:
        return Match("none")
    norm = {t: normalise(t) for t in titles}
    exact = [t for t, n in norm.items() if n == want]
    if len(exact) == 1:
        return _finalize("exact", exact[0], titles)
    learned = (aliases or {}).get(want)
    if learned in norm:
        return _finalize("alias", learned, titles)
    words = want.split()
    exact_cover = [t for t, n in norm.items() if _word_exact_covers(words, n.split())]
    if len(exact_cover) == 1:
        return _finalize("sole", exact_cover[0], titles)
    if len(exact_cover) > 1:
        ranked = sorted(exact_cover, key=lambda t: -_score(want, norm[t]))
        return Match("ambiguous", None, tuple(ranked[:3]))
    prefix_cover = [t for t, n in norm.items() if _word_prefix_covers(words, n.split())]
    if len(prefix_cover) == 1:
        return _finalize("close", prefix_cover[0], titles)
    if len(prefix_cover) > 1:
        ranked = sorted(prefix_cover, key=lambda t: -_score(want, norm[t]))
        return Match("ambiguous", None, tuple(ranked[:3]))
    scored = sorted(((_score(want, n), t) for t, n in norm.items()), reverse=True)
    close = [(s, t) for s, t in scored if s >= _CLOSE]
    if len(close) == 1 or (len(close) > 1 and close[0][0] - close[1][0] >= _LEAD):
        winner = close[0][1]
        dup = _duplicate_match(winner, titles)
        if dup is not None:
            return dup
        return Match("close", winner, tuple(t for _, t in close[:3]))
    if len(close) > 1:
        return Match("ambiguous", None, tuple(t for _, t in close[:3]))
    return Match("none", None, tuple(t for s, t in scored[:3] if s >= _NEAR))
