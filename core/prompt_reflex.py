"""Deterministic classification of coding-agent prompts and requested commands. Regex
only: microseconds, no tokens.

Authority is asymmetric: it may say STOP on its own, and may say GO only for
shapes on a tested list.

    1. ALARM   danger patterns, checked first, never allow.
    2. REFLEX  a recognised approval shape with no alarm: answer it.
    3. default anything unmatched: escalate. Unknown never means allowed.

It recognises shapes, not meaning, so the default is escalate. Every DANGER_RULES
entry has a stable rule_id and needs both a positive and a negative test.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum

# Highlighted menu marker ("❯ 1. Yes").
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


# Tier 1: irreversible, privilege-escalating, secret-touching or beyond-the-worktree
# operations, without tripping on ordinary development (a venv pip install is fine; npm
# install -g and curl | sh are not).
#
# (rule_id, pattern, reason)
DANGER_RULES: list[tuple[str, re.Pattern[str], str]] = [
    # Destructive filesystem
    ("FS_RM_RECURSIVE", re.compile(r"\brm\s+(?:-\w*\s+)*-\w*[rR]\w*[fF]|\brm\s+(?:-\w*\s+)*-\w*[fF]\w*[rR]", re.I),
     "recursive force delete"),
    ("FS_SHRED", re.compile(r"\b(?:shred|mkfs(?:\.\w+)?)\b", re.I), "unrecoverable erase / format"),
    ("FS_DD_RAW", re.compile(r"\bdd\s+if=", re.I), "raw block write"),
    # The system path is usually the destination, several arguments after the verb.
    ("FS_SYSTEM_PATH", re.compile(
        r"\b(?:rm|mv|cp|chmod|chown|tee|install|ln)\b[^\n]*?\s/(?:etc|usr|bin|sbin|boot|sys|proc|var/lib)/"
        r"|(?:>|>>)\s*/(?:etc|usr|bin|sbin|boot|sys|proc|var/lib)/", re.I),
     "write outside the project, into a system path"),
    ("FS_SHELL_RC", re.compile(r"~/\.(?:bashrc|zshrc|profile|bash_profile)\b", re.I),
     "modifies the user's shell startup files"),

    # Destructive or history-rewriting git
    ("GIT_FORCE_PUSH", re.compile(r"\bpush\b(?:.*\s)?(?:--force\b|--force-with-lease\b|-f\b)", re.I),
     "force push rewrites published history"),
    ("GIT_HARD_RESET", re.compile(r"\breset\s+--hard\b", re.I), "discards uncommitted work"),
    ("GIT_CLEAN", re.compile(r"\bgit\s+clean\s+(?:-\w*\s*)*-\w*[fdx]", re.I), "deletes untracked files"),
    # Case-sensitive: git's -d refuses unmerged work and is routine; -D forces it.
    ("GIT_BRANCH_DELETE", re.compile(r"\bbranch\b[^\n]*\s-\w*D\b"),
     "force-deletes an unmerged branch"),
    ("GIT_HISTORY_REWRITE", re.compile(r"\b(?:filter-branch|filter-repo)\b|\brebase\b.*\s--root\b", re.I),
     "rewrites repository history"),
    ("GIT_GLOBAL_CONFIG", re.compile(r"\bgit\s+config\s+--global\b", re.I),
     "changes git configuration outside the project"),

    # Privilege
    ("PRIV_SUDO", re.compile(r"(?:^|\s)(?:sudo|doas)\s", re.I), "privilege escalation"),
    ("PRIV_SU", re.compile(r"(?:^|\s)su\s+-", re.I), "switches user"),
    ("PRIV_CHMOD_WORLD", re.compile(r"\bchmod\s+(?:-\w+\s+)*777\b"), "world-writable permissions"),

    # Secrets and credentials
    ("SEC_DOTENV", re.compile(r"\.env(?:\.\w+)?\b"), "touches an environment/secret file"),
    ("SEC_KEY_MATERIAL", re.compile(r"\b(?:id_rsa|id_ed25519)\b|\.pem\b|\.p12\b|\bprivate[_-]?key\b", re.I),
     "touches private key material"),
    ("SEC_SSH_AWS", re.compile(r"~/\.(?:ssh|aws|kube|docker)/|\bcredentials\b", re.I),
     "touches stored credentials"),
    ("SEC_TOKEN_ASSIGN", re.compile(r"\b(?:api[_-]?key|secret[_-]?key|access[_-]?token|password)\s*[:=]\s*\S", re.I),
     "assigns a credential value"),

    # Database
    ("DB_DROP", re.compile(r"\bdrop\s+(?:table|database|schema)\b", re.I), "drops a database object"),
    ("DB_TRUNCATE", re.compile(r"\btruncate\s+(?:table\b|\w)", re.I), "empties a table"),
    ("DB_MIGRATE", re.compile(r"\b(?:alembic\s+(?:upgrade|downgrade)|migrate\s+(?:up|down|deploy)|db:migrate)\b", re.I),
     "runs a schema migration"),

    # Deployment and external effects. Not a bare /production/: every build prints
    # "building for production".
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

    # Host control
    ("HOST_POWER", re.compile(r"\b(?:reboot|shutdown|poweroff|halt)\b", re.I), "power state change"),
    ("HOST_SERVICE", re.compile(r"\bsystemctl\s+(?:start|stop|restart|disable|enable|mask)\b", re.I),
     "changes system services"),
    ("HOST_KILLALL", re.compile(r"\b(?:killall|pkill)\b", re.I), "mass process termination"),
]


# Tier 2: recognised approval shapes, describing the prompt's form, never its meaning.
# An alarm anywhere in the region vetoes them.
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
    """First matching danger rule as (rule_id, reason), else None. Scans the whole
    region: the dangerous part is the command above the question.
    """
    for rule_id, pattern, reason in DANGER_RULES:
        if pattern.search(region):
            return rule_id, reason
    return None


def classify(region: str) -> Decision:
    """Classify a stable terminal region into a Decision. Pure, so it can be tested
    exhaustively.
    """
    if not region or not region.strip():
        return NO_PROMPT

    danger = find_danger(region)

    # The bottom-most visible prompt is the live one.
    best: tuple[int, str, str] | None = None
    for rule_id, pattern, kind in PROMPT_SHAPES:
        matches = list(pattern.finditer(region))
        if matches and (best is None or matches[-1].start() > best[0]):
            best = (matches[-1].start(), rule_id, kind)

    if best is None:
        # Nothing is waiting: a danger pattern on screen is output, reported for logging
        # only.
        if danger:
            return Decision(Verdict.ESCALATE, None, danger[0],
                            f"dangerous content on screen ({danger[1]})")
        return NO_PROMPT

    _, shape_id, kind = best

    if danger:
        # A recognised prompt in a dangerous context: escalate.
        return Decision(Verdict.ESCALATE, None, danger[0],
                        f"{danger[1]} — needs explicit authorization")

    reply = _REPLIES.get(kind)
    if reply is None:  # "menu1" depends on the renderer, resolved by the caller
        reply = b"1" if SELECTOR_MARK in region else b"1\r"

    return Decision(Verdict.ALLOW, reply, shape_id, "recognised safe confirmation")
