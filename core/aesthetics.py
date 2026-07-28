"""The taste layer.

A landing-page mission once passed all eight of its acceptance criteria and
still looked mediocre. Nothing was broken: the criteria covered endpoints,
responsive breakpoints, aria-live regions and page weight, and the agents hit
every one. Not a single criterion mentioned how it should LOOK.

Agents deliver exactly what is measured. So taste has to arrive as data and
leave as measurable criteria, or it evaporates between the user's head and the
worktree.

Three ways in, in order of how little they ask of the user:

  1. the picker  — seven taps, no typing
  2. free text   — "like a 1970s record sleeve, warm and faded"
  3. the eagle   — asks ONE spoken question when the other two are absent

Seven questions ASKED aloud is an interrogation; seven chips TAPPED is fifteen
seconds and mildly enjoyable. Same information, opposite feeling.

THE VOCABULARY RULE
-------------------
The words the user sees are everyday words with obvious differences — Strong,
Pastel, Dark; Matte, Shiny. Not Chromatic Intensity or Tactile Finish. Someone
booking an appointment should never have to decode a design-school term to
say what they like.

The precise version lives on the RIGHT of each pair, and only the agent reads
it. Plain words facing the user, buildable spec facing the builder.
"""
from __future__ import annotations

# section key -> (label the user sees, {plain word: what it means to a builder})
#
# The right-hand side carries the weight. "Strong" tells an agent nothing;
# "saturated, high-contrast colour used confidently" is buildable and checkable.
AXES: dict[str, tuple[str, dict[str, str]]] = {
    "colors": ("Colors", {
        "Strong": "saturated, high-contrast colour used confidently across the page",
        "Pastel": "soft muted tints, low contrast, gentle and light throughout",
        "Dark": "dark grounds (near-black or deep tones) with light text on top",
        "Black & white": "no colour at all except one single accent hue",
    }),
    "mood": ("Mood", {
        "Calm": "quiet and unhurried; restrained accents, lots of empty space",
        "Fun": "warm and informal; lively colour, friendly rounded forms",
        "Fancy": "expensive and understated; precise alignment, elegant details",
        "Serious": "plain and businesslike; strict grid, no decoration",
    }),
    "surface": ("Surface", {
        "Matte": "completely flat — no shadows, no gloss, no gradients",
        "Shiny": "glossy highlights, crisp reflections, rich deep shadows",
        "Textured": "visible grain, paper or fabric texture behind the content",
        "Soft": "gentle shadows and soft layering to separate sections",
    }),
    "text": ("Text", {
        "Classic": "self-hosted serif headings with a quiet sans for body text",
        "Modern": "one clean sans across the whole page, weight for hierarchy",
        "Big & bold": "very large headline type against small body text",
        "Simple": "plain, small, evenly-sized text with minimal styling",
    }),
    "shapes": ("Shapes", {
        "Round": "generous rounded corners on cards, images and buttons",
        "Sharp": "square corners, hairline rules, strict alignment",
        "Pill": "fully-rounded buttons and chips as the signature shape",
        "Wavy": "curved dividers and arc motifs between sections",
    }),
    "pictures": ("Pictures", {
        "Photos": "real self-hosted photographs, full-width hero — NOT SVG placeholders",
        "Drawings": "consistent illustrated style for every figure",
        "Patterns": "repeating decorative patterns instead of pictures",
        "None": "no images at all — type and colour do all the work",
    }),
    "layout": ("Layout", {
        "Roomy": "large padding, short line lengths, plenty of breathing room",
        "Normal": "comfortable conventional spacing",
        "Tight": "compact and information-dense, more visible at once",
        "Full-screen": "each section fills the screen, one idea at a time",
    }),
}

ORDER = list(AXES.keys())


def options() -> list[dict]:
    """Shape the UI picker renders from."""
    return [{"key": k, "label": AXES[k][0], "words": list(AXES[k][1].keys())}
            for k in ORDER]


def brief_from_choices(choices: dict) -> str:
    """Turn picked words into a brief an architect can encode as criteria.

    Missing sections are skipped rather than defaulted — a half-filled picker
    is still better direction than none, and inventing the rest would put words
    in the user's mouth.
    """
    lines = []
    for key in ORDER:
        word = (choices or {}).get(key)
        if not word:
            continue
        label, words = AXES[key]
        meaning = words.get(word)
        if meaning:
            lines.append(f"- {label}: {word} — {meaning}")
    if not lines:
        return ""
    return ("The user chose this look:\n" + "\n".join(lines) +
            "\nHold to it on every surface, and name the exact hex values and "
            "typefaces you derive from it in the acceptance criteria.")


def brief_from_text(text: str) -> str:
    """Free-text taste, passed through nearly untouched — the user's own words
    carry nuance no fixed vocabulary can ("like a 1970s record sleeve, warm and faded")."""
    text = (text or "").strip()
    if not text:
        return ""
    return (f"The user described the look they want, in their own words:\n"
            f"\"{text}\"\n"
            f"Interpret it faithfully and translate it into exact palette hex "
            f"values, typefaces and spacing in the acceptance criteria.")


def the_one_question() -> str:
    """What the eagle asks when nothing else is available.

    ONE question, with named options. Open questions ("what aesthetic do you
    want?") make people work; three concrete choices are answerable instantly
    and still cover most of the space.
    """
    return ("Any look in mind — soft and pastel, dark and shiny, "
            "or clean and simple? I can also just pick what suits the business.")


def brief_from_answer(answer: str) -> str:
    """Map a spoken reply onto the picker vocabulary where it lands cleanly,
    otherwise keep the user's own phrasing."""
    a = (answer or "").strip().lower()
    if not a:
        return ""
    shorthand = {
        ("soft", "pastel"): {"colors": "Pastel", "mood": "Calm", "surface": "Soft",
                             "text": "Classic", "shapes": "Round",
                             "pictures": "Photos", "layout": "Roomy"},
        ("dark", "shiny"): {"colors": "Dark", "mood": "Fancy", "surface": "Shiny",
                            "text": "Big & bold", "shapes": "Pill",
                            "pictures": "Photos", "layout": "Full-screen"},
        ("clean", "simple"): {"colors": "Black & white", "mood": "Serious",
                              "surface": "Matte", "text": "Modern",
                              "shapes": "Sharp", "pictures": "None",
                              "layout": "Normal"},
    }
    for words, choices in shorthand.items():
        if all(w in a for w in words):
            return brief_from_choices(choices)
    return brief_from_text(answer)
