"""Reviewer Agent: offloaded verification and automated merges.

Eagle's live executive brain never reads multi-thousand-line diffs.
When a swarm branch is ready, the reviewer worker:

  1. Checkpoints any uncommitted worktree changes.
  2. Builds a diff summary (files/insertions/deletions) vs main.
  3. Verifies changed Python files compile; runs pytest if the project
     has tests (bounded, quiet).
  4. Optionally delegates a deep review to a one-shot agent CLI
     (`claude -p`) — Tier 2, off by default.
  5. Merges the branch into main (--no-ff); on conflict it aborts
     cleanly and reports the conflicting files instead.

Returns a concise voice-ready summary either way.
"""

import fnmatch
import py_compile
import shutil
import subprocess
from pathlib import Path


def _owner_of(path: str, owners: dict) -> str | None:
    """Which workstream owns this file, per the plan's `owns` globs.

    Matches a bare directory ("server/") as a prefix as well as a real glob
    ("server/**"), because architects write both and a plan should not fail to
    merge over glob punctuation. Returns None when nobody owns it, or when two
    workstreams both claim it — an ambiguous claim is not a decision.
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


def _run(args, cwd, timeout=60):
    return subprocess.run(args, cwd=str(cwd), capture_output=True,
                          text=True, timeout=timeout)


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

    # ------------------------------------------------------------- review

    def review(self, agent_key: str, branch: str, worktree: Path,
               deep: bool = False) -> dict:
        report = {"agent": agent_key, "branch": branch, "ok": True,
                  "notes": [], "diffstat": "", "llm": ""}
        worktree = Path(worktree)

        # 1. Checkpoint uncommitted work so nothing is lost or half-merged.
        if self._git("status", "--porcelain", cwd=worktree).stdout.strip():
            self._git("add", "-A", cwd=worktree)
            self._git("commit", "-m", f"swarm({agent_key}): checkpoint",
                      cwd=worktree)
            report["notes"].append("uncommitted changes checkpointed")

        base = self._main_branch()
        stat = self._git("diff", "--stat", f"{base}...{branch}").stdout.strip()
        report["diffstat"] = stat.splitlines()[-1] if stat else "no changes"

        # 2. Changed Python files must at least compile.
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

        # 3. Run the project's tests when it visibly has some.
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

        # 4. Optional deep LLM review, fully offloaded.
        if deep and shutil.which(LLM_REVIEWER[0]):
            diff = self._git("diff", f"{base}...{branch}").stdout[:24000]
            try:
                r = _run([*LLM_REVIEWER,
                          "Review this git diff for bugs, security issues and "
                          "broken interfaces. Reply with VERDICT: APPROVE or "
                          "VERDICT: REJECT plus max 3 bullet findings.\n\n" + diff],
                         worktree, LLM_REVIEW_TIMEOUT_S)
                report["llm"] = r.stdout.strip()[:600]
                if "VERDICT: REJECT" in r.stdout:
                    report["ok"] = False
                    report["notes"].append("deep review rejected the diff")
            except (subprocess.TimeoutExpired, OSError):
                report["notes"].append("deep review unavailable")
        return report

    # -------------------------------------------------------------- merge

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
        """Settle conflicts the plan already decided, escalate the rest.

        Every workstream declares `owns` globs, and they are disjoint by
        construction — the validator rejects plans without ownership. So a
        conflicting file is either:

          * inside this workstream's lane → it is the author, take its version;
          * inside somebody else's lane   → this agent wrote outside its
            boundary, so keep the owner's version;
          * owned by nobody               → genuinely ambiguous, escalate.

        This is deterministic and costs no tokens: the ownership contract IS
        the merge policy, and the previous code threw that information away and
        aborted on every conflict, stranding finished work on its branch.
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
        """workstream id -> its owns globs, straight from the executing plan."""
        try:
            import json
            pf = self.root / ".space_eagle" / "plan.json"
            plan = json.loads(pf.read_text(encoding="utf-8"))
            return {w["id"]: (w.get("owns") or [])
                    for w in plan.get("workstreams", [])}
        except (OSError, ValueError, KeyError, TypeError) as e:
            # Narrow on purpose: a bare `except Exception` here hid an
            # AttributeError for months-worth of debugging value — every
            # conflict silently fell through to "abort" with no clue why.
            print(f"[swarm_reviewer] no ownership map ({e}); "
                  f"conflicts will escalate instead of resolving")
            return {}

    # ---------------------------------------------------------- top level

    def review_and_merge(self, agent_key: str, branch: str, worktree,
                         deep: bool = False) -> str:
        self._log(f"SYS: Reviewer checking '{agent_key}' ({branch})...")
        rep = self.review(agent_key, branch, worktree, deep=deep)
        summary = f"{agent_key}: {rep['diffstat']}"
        if rep["notes"]:
            summary += f" [{'; '.join(rep['notes'])}]"
        if not rep["ok"]:
            # A caught error → shared lesson so no agent repeats it. Only the
            # failure notes (syntax/tests/deep-review) become lessons, not the
            # benign ones (e.g. "tests passed").
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
