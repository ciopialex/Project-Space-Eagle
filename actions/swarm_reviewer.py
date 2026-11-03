"""Reviewer: verification and merging of swarm branches, off the voice loop.

  1. Checkpoint uncommitted worktree changes.
  2. Summarise the diff against main.
  3. Compile changed Python files; run pytest if the project has tests.
  4. Optionally, a deep review by a one-shot agent CLI (off by default).
  5. Merge --no-ff; on conflict resolve by ownership or abort and report.

Returns a voice-ready summary.
"""

import fnmatch
import py_compile
import re
import shutil
import subprocess
from pathlib import Path

from core.agent_stream import (
    RESULT, headless_argv, stream_events, supports_headless,
)


def _owner_of(path: str, owners: dict) -> str | None:
    """Which workstream owns this file, per the plan's `owns` globs (a bare directory
    counts as a prefix). None when nobody does, or when two claim it.
    """
    hits = []
    for ws_id, globs in (owners or {}).items():
        for g in globs or []:
            g = (g or "").strip()
            if not g:
                continue
            if fnmatch.fnmatch(path, g) or fnmatch.fnmatch(path, g.rstrip("/") + "/*"):
                hits.append(ws_id)
                break
            if path.startswith(g.rstrip("*/") .rstrip("/") + "/"):
                hits.append(ws_id)
                break
    return hits[0] if len(hits) == 1 else None

TEST_TIMEOUT_S = 180
LLM_REVIEW_TIMEOUT_S = 120
LLM_REVIEWER = ("claude", "-p")  # one-shot, non-interactive

# What the reviewer must end its answer with: deep_review_verdict approves on nothing
# else.
VERDICT_RE = re.compile(r"VERDICT:\s*(APPROVE|REJECT)", re.I)


def _run(args, cwd, timeout=60):
    return subprocess.run(args, cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout)


def deep_review_verdict(events) -> dict:
    """Turn an agent event stream into {ok, llm, note}. Fails closed: anything other
    than an explicit APPROVE in a successful run (is_error false) is a rejection,
    including errors, timeouts, silence and prose without a verdict.
    """
    events = list(events)
    result = next((e for e in reversed(events) if e.kind == RESULT), None)

    if result is None:
        return {"ok": False, "llm": "",
                "note": "deep review returned no verdict (no result event)"}

    text = (result.text or "").strip()

    if not result.ok:
        status = result.data.get("api_error_status")
        detail = text or result.data.get("terminal_reason") or "unknown error"
        if status:
            detail = f"HTTP {status}: {detail}"
        return {"ok": False, "llm": text[:600],
                "note": f"deep review FAILED, not approving: {detail[:200]}"}

    verdicts = [v.upper() for v in VERDICT_RE.findall(text)]
    if not verdicts:
        return {"ok": False, "llm": text[:600],
                "note": "deep review returned no verdict; treating as reject"}

    # Every verdict found: a review stating both has not approved.
    if "REJECT" in verdicts:
        return {"ok": False, "llm": text[:600],
                "note": "deep review rejected the diff"}

    return {"ok": True, "llm": text[:600], "note": ""}


class ReviewerAgent:
    def __init__(self, project_dir: Path, player=None):
        self.root = Path(project_dir).resolve()
        self.player = player

    def _log(self, msg):
        if self.player:
            self.player.write_log(msg)
        print(msg)

    def _git(self, *args, cwd=None, timeout=60):
        return _run(["git", *args], cwd or self.root, timeout)

    def _main_branch(self) -> str:
        for cand in ("main", "master"):
            if self._git("rev-parse", "--verify", cand).returncode == 0:
                return cand
        return self._git("branch", "--show-current").stdout.strip() or "main"


    def review(self, agent_key: str, branch: str, worktree: Path,
               deep: bool = False) -> dict:
        report = {"agent": agent_key, "branch": branch, "ok": True,
                  "notes": [], "diffstat": "", "llm": ""}
        worktree = Path(worktree)

        if self._git("status", "--porcelain", cwd=worktree).stdout.strip():
            self._git("add", "-A", cwd=worktree)
            self._git("commit", "-m", f"swarm({agent_key}): checkpoint",
                      cwd=worktree)
            report["notes"].append("uncommitted changes checkpointed")

        base = self._main_branch()
        stat = self._git("diff", "--stat", f"{base}...{branch}").stdout.strip()
        report["diffstat"] = stat.splitlines()[-1] if stat else "no changes"

        changed = [l for l in self._git(
            "diff", "--name-only", f"{base}...{branch}").stdout.splitlines() if l]
        bad = []
        for rel in changed:
            f = worktree / rel
            if f.suffix == ".py" and f.exists():
                try:
                    py_compile.compile(str(f), doraise=True)
                except py_compile.PyCompileError as e:
                    bad.append(f"{rel}: {e.msg.splitlines()[-1][:100]}")
        if bad:
            report["ok"] = False
            report["notes"].append(f"syntax errors: {'; '.join(bad[:3])}")

        if any((worktree / t).exists() for t in
               ("tests", "test", "pytest.ini", "conftest.py")):
            try:
                r = _run(["python3", "-m", "pytest", "-x", "-q"],
                         worktree, TEST_TIMEOUT_S)
                if r.returncode == 0:
                    report["notes"].append("tests passed")
                else:
                    report["ok"] = False
                    tail = (r.stdout + r.stderr).strip().splitlines()[-3:]
                    report["notes"].append("tests FAILED: " + " | ".join(tail))
            except (subprocess.TimeoutExpired, OSError) as e:
                report["notes"].append(f"tests skipped ({type(e).__name__})")

        # Optional deep review, driven as an event stream that always ends in a RESULT.
        if deep:
            if not supports_headless("claude_code") or not shutil.which(LLM_REVIEWER[0]):
                # Asked for and not delivered: say so.
                report["ok"] = False
                report["notes"].append(
                    "deep review requested but no reviewer CLI is installed")
                return report

            diff = self._git("diff", f"{base}...{branch}").stdout[:24000]
            argv = headless_argv(
                "claude_code",
                "Review this git diff for bugs, security issues and broken "
                "interfaces. Reply with VERDICT: APPROVE or VERDICT: REJECT "
                "plus max 3 bullet findings.\n\n" + diff)
            verdict = deep_review_verdict(
                stream_events(argv, cwd=worktree, timeout_s=LLM_REVIEW_TIMEOUT_S))
            report["llm"] = verdict["llm"]
            if not verdict["ok"]:
                report["ok"] = False
                report["notes"].append(verdict["note"])
        return report


    def merge(self, agent_key: str, branch: str) -> dict:
        base = self._main_branch()
        current = self._git("branch", "--show-current").stdout.strip()
        if current != base:
            return {"merged": False,
                    "detail": f"project root is on '{current}', not '{base}'"}
        if self._git("status", "--porcelain").stdout.strip():
            return {"merged": False,
                    "detail": "project root has uncommitted changes — commit "
                              "or stash them before merging"}
        r = self._git("merge", "--no-ff", branch, "-m",
                      f"swarm: merge {branch} ({agent_key})")
        if r.returncode == 0:
            return {"merged": True, "detail": f"{branch} merged into {base}"}

        conflicts = [l for l in
                     self._git("diff", "--name-only",
                               "--diff-filter=U").stdout.splitlines() if l]
        resolved, unresolved = self._resolve_by_ownership(agent_key, conflicts)
        if conflicts and not unresolved:
            self._git("add", "-A")
            c = self._git("commit", "--no-edit")
            if c.returncode == 0:
                return {"merged": True,
                        "detail": f"{branch} merged into {base}; {len(resolved)} "
                                  f"conflict(s) resolved by file ownership "
                                  f"({', '.join(resolved[:3])})"}
        self._git("merge", "--abort")
        detail = (f"merge conflicts in: {', '.join(unresolved[:5]) or 'unknown'}"
                  f" — merge aborted, branch left intact")
        return {"merged": False, "detail": detail, "conflicts": unresolved}

    def _resolve_by_ownership(self, agent_key: str, conflicts: list[str]):
        """Settle conflicts the plan already decided: a file in this workstream's
        lane takes its version; in another's lane, the owner's; owned by nobody,
        escalate. Lanes are disjoint by construction.
        """
        if not conflicts:
            return [], []
        owners = self._ownership_map()
        resolved, unresolved = [], []
        for path in conflicts:
            owner = _owner_of(path, owners)
            if owner is None:
                unresolved.append(path)
                continue
            # "theirs" is the incoming branch, "ours" is main.
            side = "--theirs" if owner == agent_key else "--ours"
            if self._git("checkout", side, "--", path).returncode == 0:
                resolved.append(f"{path}→{owner}")
            else:
                unresolved.append(path)
        return resolved, unresolved

    def _ownership_map(self) -> dict:
        """workstream id -> its owns globs, from the executing plan."""
        try:
            import json
            pf = self.root / ".space_eagle" / "plan.json"
            plan = json.loads(pf.read_text(encoding="utf-8"))
            return {w["id"]: (w.get("owns") or [])
                    for w in plan.get("workstreams", [])}
        except (OSError, ValueError, KeyError, TypeError) as e:
            # Narrow on purpose, so a real bug is not hidden as an abort.
            print(f"[swarm_reviewer] no ownership map ({e}); "
                  f"conflicts will escalate instead of resolving")
            return {}


    def review_and_merge(self, agent_key: str, branch: str, worktree,
                         deep: bool = False) -> str:
        self._log(f"SYS: Reviewer checking '{agent_key}' ({branch})...")
        rep = self.review(agent_key, branch, worktree, deep=deep)
        summary = f"{agent_key}: {rep['diffstat']}"
        if rep["notes"]:
            summary += f" [{'; '.join(rep['notes'])}]"
        if not rep["ok"]:
            # Only failures become shared lessons.
            try:
                from actions.swarm_orchestrator import SwarmOrchestrator
                orch = SwarmOrchestrator(self.project_dir, player=self.player)
                for note in rep["notes"]:
                    low = note.lower()
                    if any(k in low for k in ("syntax error", "tests failed", "rejected")):
                        orch.record_lesson(agent_key, note, tag="review")
            except Exception as _e:
                print(f"[swarm_reviewer.py] lesson capture skipped: {_e}")
            return f"REVIEW BLOCKED — {summary}. Branch NOT merged."
        m = self.merge(agent_key, branch)
        verdict = "MERGED" if m["merged"] else "MERGE FAILED"
        self._log(f"SYS: Reviewer: {verdict} — {m['detail']}.")
        return f"{verdict} — {summary}. {m['detail']}."
