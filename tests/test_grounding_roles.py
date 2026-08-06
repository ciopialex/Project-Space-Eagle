import pytest

from actions.grounding.base import match_score
from actions.grounding.roles import (LINUX, MACOS, WINDOWS, best_match,
                                     normalize)
from actions.grounding.atspi import AtspiNode


# ---- normalization ------------------------------------------------------

def test_linux_roles_are_already_canonical():
    assert normalize("push button", LINUX) == "push button"
    assert normalize("page tab", LINUX) == "page tab"


def test_windows_control_types_normalize():
    assert normalize("Button", WINDOWS) == "push button"
    assert normalize("Edit", WINDOWS) == "text"
    assert normalize("CheckBox", WINDOWS) == "check box"
    assert normalize("MenuItem", WINDOWS) == "menu item"
    assert normalize("TabItem", WINDOWS) == "page tab"
    assert normalize("Hyperlink", WINDOWS) == "link"


def test_macos_roles_normalize_with_or_without_the_ax_prefix():
    assert normalize("AXButton", MACOS) == "push button"
    assert normalize("Button", MACOS) == "push button"
    assert normalize("AXTextField", MACOS) == "text"
    assert normalize("AXSecureTextField", MACOS) == "password text"
    assert normalize("AXMenuItem", MACOS) == "menu item"
    assert normalize("AXStaticText", MACOS) == "label"


def test_unknown_roles_pass_through_lowercased_not_dropped():
    """An unrecognised control still matches on its name."""
    assert normalize("SomeNewControl", WINDOWS) == "somenewcontrol"
    assert normalize("AXFancyThing", MACOS) == "fancything"


def test_empty_and_none_roles_are_safe():
    assert normalize("", WINDOWS) == ""
    assert normalize(None, MACOS) == ""


def test_unknown_platform_leaves_the_role_alone():
    assert normalize("Button", "plan9") == "button"


# ---- the reason this module exists --------------------------------------

@pytest.mark.parametrize("platform,role", [
    (WINDOWS, "Button"),
    (MACOS, "AXButton"),
    (LINUX, "push button"),
])
def test_the_same_phrase_matches_a_button_on_every_platform(platform, role):
    """Without normalization, "the Save button" scores 0.0 on Windows and
    macOS because match_score only knows AT-SPI vocabulary."""
    assert match_score("the Save button", "Save",
                       normalize(role, platform)) == pytest.approx(1.0)


def test_raw_platform_roles_would_have_scored_lower():
    """Proof the gap was real: the un-normalized role gets no role bonus."""
    assert match_score("the Save button", "Save", "Button") == pytest.approx(0.8)
    assert match_score("the Save button", "Save", "AXButton") == pytest.approx(0.8)


# ---- shared matching ----------------------------------------------------

def _nodes():
    return [
        AtspiNode("Cancel", "Button", 100, 500, 80, 30),
        AtspiNode("Save",   "Button", 200, 500, 80, 30),
        AtspiNode("Save",   "MenuItem", 0, 10, 60, 20),
        AtspiNode("Hidden", "Button", 0, 0, 0, 0),
    ]


def test_best_match_normalizes_before_scoring():
    node = best_match(_nodes(), "the Save button", platform=WINDOWS)
    assert node is not None
    assert node.name == "Save"
    assert node.left == 200


def test_best_match_role_hint_disambiguates_across_platforms():
    assert best_match(_nodes(), "the Save menu", platform=WINDOWS).top == 10
    assert best_match(_nodes(), "the Save button", platform=WINDOWS).top == 500


def test_best_match_returns_none_below_threshold():
    assert best_match(_nodes(), "the Frobnicate button", platform=WINDOWS) is None


def test_best_match_rejects_zero_sized_nodes():
    assert best_match(_nodes(), "Hidden button", platform=WINDOWS) is None


def test_best_match_rejects_sentinel_coordinates():
    bogus = [AtspiNode("Save", "Button", -2147483648, -2147483648, 80, 30)]
    assert best_match(bogus, "the Save button", platform=WINDOWS) is None


def test_best_match_survives_a_malformed_node():
    class Broken:
        name = "Save"
        role = "Button"

        @property
        def width(self):
            raise RuntimeError("no bounds")

    assert best_match([Broken()], "the Save button", platform=WINDOWS) is None


# --- Diacritics must not shatter words (found by benching emag.ro) --------

def test_accented_words_stay_whole_instead_of_splitting():
    """`_tokens` splits on [^a-z0-9]+, which made every accented character a
    word *separator*: Romanian "căutare" became "c" + "utare". The emag.ro
    search box is perceived correctly as "Începe o nouă căutare" and was
    still unreachable, because the matcher could not see the word."""
    from actions.grounding.base import _tokens
    assert _tokens("Începe o nouă căutare") == ["incepe", "o", "noua",
                                                "cautare"]
    assert _tokens("câmpul de căutare") == ["campul", "de", "cautare"]
    assert _tokens("Plătește") == ["plateste"]
    # Sites spell the same word both ways; both must tokenise identically.
    assert _tokens("Plateste") == _tokens("Plătește")


def test_a_romanian_description_matches_a_romanian_label():
    from actions.grounding.base import match_score
    # Was 0.27 against a 0.5 threshold — below the bar, so unreachable.
    assert match_score("câmpul de căutare",
                       "Începe o nouă căutare", "text") >= 0.5


def test_romanian_and_spanish_filler_words_are_stripped():
    """Filler is language-specific. With an English-only list, "campul" and
    "de" counted as meaningful words and dragged every Romanian score down."""
    from actions.grounding.base import match_score
    assert match_score("butonul de salvare", "Salvare", "push button") >= 0.5
    assert match_score("el campo de nombre", "Nombre", "text") >= 0.5


def test_english_matching_is_unchanged_by_the_folding():
    from actions.grounding.base import match_score
    assert match_score("the Sign in button", "Sign in", "push button") == 1.0
    assert match_score("the Save button", "Save", "push button") == 1.0
    assert match_score("the parachute", "Sign in", "push button") == 0.0


def test_a_combobox_counts_as_a_field():
    """Modern search inputs use the autocomplete pattern — the box you type
    into is role=combobox, not textbox. Without this, DuckDuckGo's search
    input tied at 0.80 with sixteen buttons, links and images."""
    from actions.grounding.base import match_score
    assert match_score("the search field",
                       "Search with DuckDuckGo", "combo box") == 1.0
