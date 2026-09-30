"""Reflex layer — deterministic classification of agent terminal prompts.

The swarm's coding agents block on interactive confirmations ("Run this
command? [y/N]", "1. Yes  2. No"). Answering them is what lets a mission run
unattended. Answering them BLINDLY is what makes running unattended unsafe:
`Delete production database? [y/N]` and `Create file? [y/N]` are the same
string to a naive matcher.

This module is the fast, model-free tier of the decision path. It is regex-
only by design — a match costs about a microsecond, roughly six orders of
magnitude less than an inference call, so the common case never burns tokens.

AUTHORITY IS DELIBERATELY ASYMMETRIC
------------------------------------
    The reflex may say STOP on its own authority.
    It may only say GO for shapes on an explicit, tested, proven-safe list.

Blocking is cheap and reversible (worst case: the human is asked something
they would have waved through). Allowing is neither. So the tiers are not a
ladder — they are a veto followed by a whitelist:

    1. ALARM    danger patterns. Checked FIRST, wins every tie, never allows.
    2. REFLEX   a recognised approval shape AND no alarm  ->  auto-answer.
    3. default  anything unmatched  ->  ESCALATE. Unknown never means allowed.

WHAT THIS TIER DOES NOT DO
--------------------------
It does not understand the prompt. It recognises *shapes*. Coverage is a long
tail that never fully closes, and pattern matching is structurally weakest on
exactly the rare, novel prompts that matter most — which is why the default is
ESCALATE rather than ALLOW.

Every rule in DANGER_RULES carries a stable `rule_id` for the audit ledger, and
every rule is required to have both a positive test (it catches the bad case)
and a negative test (it spares an ordinary one). Rules added without the
negative test accumulate into scar tissue that quietly paralyses the swarm.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Ink/Rich-style highlighted menu marker (e.g. "❯ 1. Yes")
SELECTOR_MARK = "❯"


class Verdict(str, Enum):
    """Outcome of classifying one terminal screen region."""

    ALLOW = "allow"          # recognised safe shape — reply is set
    ESCALATE = "escalate"    # dangerous or unrecognised — a human/controller decides
    NO_PROMPT = "no_prompt"  # nothing is waiting on input


@dataclass(frozen=True)
class Decision:
    """Typed, auditable result. `reply` is only ever set when verdict is ALLOW."""

    verdict: Verdict
    reply: bytes | None
    rule_id: str
    reason: str

    @property
    def is_allow(self) -> bool:
        return self.verdict is Verdict.ALLOW


# --------------------------------------------------------------- tier 1: alarm
#
# Irreversible, privilege-escalating, secret-touching, or blast-radius-beyond-
# the-worktree operations. Tuned to catch genuinely dangerous work WITHOUT
# tripping on ordinary development: `pip install` inside a project venv is
# routine and reversible, so it is absent here, while `npm install -g` (system
# wide) and `curl | sh` (executing unverified remote code) are present.
#
# (rule_id, pattern, human-readable reason)
DANGER_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # --- destructive filesystem -------------------------------------------
    ("FS_RM_RECURSIVE", re.compile(r"\brm\s+(?:-\w*\s+)*-\w*[rR]\w*[fF]|\brm\s+(?:-\w*\s+)*-\w*[fF]\w*[rR]", re.I),
     "recursive force delete"),
    ("FS_SHRED", re.compile(r"\b(?:shred|mkfs(?:\.\w+)?)\b", re.I), "unrecoverable erase / format"),
    ("FS_DD_RAW", re.compile(r"\bdd\s+if=", re.I), "raw block write"),
    # The system path is usually the *destination*, so it can sit several
    # arguments after the verb ("cp nginx.conf /etc/nginx/").
    ("FS_SYSTEM_PATH", re.compile(
        r"\b(?:rm|mv|cp|chmod|chown|tee|install|ln)\b[^\n]*?\s/(?:etc|usr|bin|sbin|boot|sys|proc|var/lib)/"
        r"|(?:>|>>)\s*/(?:etc|usr|bin|sbin|boot|sys|proc|var/lib)/", re.I),
     "write outside the project, into a system path"),
    ("FS_SHELL_RC", re.compile(r"~/\.(?:bashrc|zshrc|profile|bash_profile)\b", re.I),
     "modifies the user's shell startup files"),

    # --- destructive / history-rewriting git --------------------------------
    ("GIT_FORCE_PUSH", re.compile(r"\bpush\b(?:.*\s)?(?:--force\b|--force-with-lease\b|-f\b)", re.I),
     "force push rewrites published history"),
    ("GIT_HARD_RESET", re.compile(r"\breset\s+--hard\b", re.I), "discards uncommitted work"),
    ("GIT_CLEAN", re.compile(r"\bgit\s+clean\s+(?:-\w*\s*)*-\w*[fdx]", re.I), "deletes untracked files"),
    # Case-SENSITIVE on purpose: git's `-d` refuses to delete unmerged work and
    # is routine; `-D` forces it. re.I here would block every ordinary cleanup.
    ("GIT_BRANCH_DELETE", re.compile(r"\bbranch\b[^\n]*\s-\w*D\b"),
     "force-deletes an unmerged branch"),
    ("GIT_HISTORY_REWRITE", re.compile(r"\b(?:filter-branch|filter-repo)\b|\brebase\b.*\s--root\b", re.I),
     "rewrites repository history"),
    ("GIT_GLOBAL_CONFIG", re.compile(r"\bgit\s+config\s+--global\b", re.I),
     "changes git configuration outside the project"),

    # --- privilege ----------------------------------------------------------
    ("PRIV_SUDO", re.compile(r"(?:^|\s)(?:sudo|doas)\s", re.I), "privilege escalation"),
    ("PRIV_SU", re.compile(r"(?:^|\s)su\s+-", re.I), "switches user"),
    ("PRIV_CHMOD_WORLD", re.compile(r"\bchmod\s+(?:-\w+\s+)*777\b"), "world-writable permissions"),

    # --- secrets and credentials -------------------------------------------
    ("SEC_DOTENV", re.compile(r"\.env(?:\.\w+)?\b"), "touches an environment/secret file"),
    ("SEC_KEY_MATERIAL", re.compile(r"\b(?:id_rsa|id_ed25519)\b|\.pem\b|\.p12\b|\bprivate[_-]?key\b", re.I),
     "touches private key material"),
    ("SEC_SSH_AWS", re.compile(r"~/\.(?:ssh|aws|kube|docker)/|\bcredentials\b", re.I),
     "touches stored credentials"),
    ("SEC_TOKEN_ASSIGN", re.compile(r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[:=]\s*\S", re.I),
     "assigns a credential value"),

    # --- database -----------------------------------------------------------
    ("DB_DROP", re.compile(r"\bdrop\s+(?:table|database|schema)\b", re.I), "drops a database object"),
    ("DB_TRUNCATE", re.compile(r"\btruncate\s+(?:table\b|\w)", re.I), "empties a table"),
    ("DB_MIGRATE", re.compile(r"\b(?:alembic\s+(?:upgrade|downgrade)|migrate\s+(?:up|down|deploy)|db:migrate)\b", re.I),
     "runs a schema migration"),

    # --- deployment and external effects ------------------------------------
    # Deliberately NOT a bare /production/: every frontend build prints
    # "building for production", and a rule that fires on that would stall the
    # swarm constantly. Match production *operations*, not the word.
    ("EXT_PRODUCTION", re.compile(
        r"\b(?:deploy|push|release|migrate|drop|delete|restart|rollback|wipe)\b[^\n]{0,40}\bprod(?:uction)?\b"
        r"|\bprod(?:uction)?\b[^\n]{0,40}\b(?:database|db|server|cluster|deploy|bucket)\b", re.I),
     "operates on a production environment"),
    ("EXT_DEPLOY", re.compile(r"\b(?:deploy|terraform\s+(?:apply|destroy)|kubectl\s+delete|helm\s+(?:install|upgrade))\b", re.I),
     "changes deployed infrastructure"),
    ("EXT_PUBLISH", re.compile(r"\b(?:npm|yarn|pnpm)\s+publish\b|\btwine\s+upload\b|\bcargo\s+publish\b", re.I),
     "publishes a package to a public registry"),
    ("EXT_PIPE_TO_SHELL", re.compile(r"(?:curl|wget)\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|)sh\b", re.I),
     "executes unverified remote code"),
    ("EXT_GLOBAL_INSTALL", re.compile(r"\b(?:npm|yarn|pnpm)\s+(?:install|add)\b[^\n]*\s-g\b|\bnpm\s+i\s+-g\b", re.I),
     "installs a package system-wide"),

    # --- host control -------------------------------------------------------
    ("HOST_POWER", re.compile(r"\b(?:reboot|shutdown|poweroff|halt)\b", re.I), "power state change"),
    ("HOST_SERVICE", re.compile(r"\bsystemctl\s+(?:start|stop|restart|disable|enable|mask)\b", re.I),
     "changes system services"),
    ("HOST_KILLALL", re.compile(r"\b(?:killall|pkill)\b", re.I), "mass process termination"),
]


# -------------------------------------------------------------- tier 2: reflex
#
# Recognised approval shapes. These describe the *form* of the prompt, never
# its meaning — which is precisely why an ALARM hit anywhere in the same region
# vetoes them.
#
# (rule_id, pattern, reply-kind)
PROMPT_SHAPES: list[tuple[str, re.Pattern[str], str]] = [
    ("SHAPE_MENU_YES", re.compile(
        rf"^\s*(?:{re.escape(SELECTOR_MARK)}\s*)?1[.)]\s+"
        r"(?:yes|accept|approve|allow|proceed|continue|trust|confirm)",
        re.I | re.M), "menu1"),
    ("SHAPE_YN", re.compile(r"\[y/N\]|\[Y/n\]|\(y/n\)", re.I), "yes"),
    ("SHAPE_PRESS_ENTER", re.compile(r"press\s+enter\s+to\s+continue", re.I), "enter"),
]

_REPLIES: dict[str, bytes] = {"yes": b"y\r", "enter": b"\r"}

NO_PROMPT = Decision(Verdict.NO_PROMPT, None, "NONE", "no prompt awaiting input")


def find_danger(region: str) -> tuple[str, str] | None:
    """First matching danger rule as (rule_id, reason), else None.

    Scans the WHOLE region rather than just the prompt line: the confirmation
    itself ("Proceed? [y/N]") is never the dangerous part — the command printed
    above it is.
    """
    for rule_id, pattern, reason in DANGER_RULES:
        if pattern.search(region):
            return rule_id, reason
    return None


def classify(region: str) -> Decision:
    """Classify a stable terminal region into a typed, auditable Decision.

    Pure and side-effect free, so it is cheap to unit test exhaustively —
    which is the point of keeping this tier free of both I/O and inference.
    """
    if not region or not region.strip():
        return NO_PROMPT

    # --- tier 1: alarm has veto power and is checked first ------------------
    danger = find_danger(region)

    # --- tier 2: is anything actually waiting on input? ---------------------
    # Several prompts can be visible at once; the bottom-most is the live one.
    best: tuple[int, str, str] | None = None
    for rule_id, pattern, kind in PROMPT_SHAPES:
        matches = list(pattern.finditer(region))
        if matches and (best is None or matches[-1].start() > best[0]):
            best = (matches[-1].start(), rule_id, kind)

    if best is None:
        # Nothing recognisable is blocking. If a danger pattern is on screen
        # it is output, not a question — report it so the caller can log, but
        # there is nothing to answer.
        if danger:
            return Decision(Verdict.ESCALATE, None, danger[0],
                            f"dangerous content on screen ({danger[1]})")
        return NO_PROMPT

    _, shape_id, kind = best

    if danger:
        # A recognised prompt, but the surrounding context is dangerous.
        # This is the case the old blind matcher got wrong.
        return Decision(Verdict.ESCALATE, None, danger[0],
                        f"{danger[1]} — needs explicit authorization")

    reply = _REPLIES.get(kind)
    if reply is None:  # "menu1" depends on the renderer, resolved by the caller
        reply = b"1" if SELECTOR_MARK in region else b"1\r"

    return Decision(Verdict.ALLOW, reply, shape_id, "recognised safe confirmation")
