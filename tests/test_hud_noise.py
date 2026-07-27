"""Keeping agent TUI churn out of the operator's face.

Run:  .venv/bin/python -m pytest tests/ -q

Observed live: the eagle's terminal and the UI log filled with hundreds of
near-identical lines while agents worked —

    [AntigravityCLI] Gen
    [AntigravityCLI] Gene
    [AntigravityCLI] Gener
    [ClaudeCode] 1thinking
    [ClaudeCode] 2thinking
    [ClaudeCode] ✱still thinking

Agent CLIs are full-screen TUIs that repaint a status line constantly, and the
PTY reports every repaint as another line. Exact-match deduping could never
catch it: each repaint is a genuinely different string. You could literally
watch the word "Generating" being typed one character at a time, one log line
per character.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from actions.agent_delegation import AgentAdapter  # noqa: E402


class Recorder:
    def __init__(self):
        self.lines: list[str] = []

    def write_log(self, msg: str):
        self.lines.append(msg)


def pump():
    """A bare adapter is enough — _hud_pump only needs .name."""
    a = object.__new__(AgentAdapter)
    a.name = "TestAgent"
    rec = Recorder()
    return a._hud_pump(rec), rec


def test_progressive_redraw_collapses_to_one_line():
    """The exact flood from the screenshots."""
    on_line, rec = pump()
    for frag in ["Gen", "Gene", "Gener", "Generat", "Generating", "Generating…"]:
        on_line(frag)
    assert len(rec.lines) == 1, f"expected 1 line, got {rec.lines}"


def test_spinner_counters_collapse():
    """1thinking / 2thinking / ✱still thinking are one status, not six."""
    on_line, rec = pump()
    for s in ["1thinking", "2thinking", "3thinking", "✱thinking",
              "✻still thinking", "10s · thinking)"]:
        on_line(s)
    assert len(rec.lines) <= 1, f"spinner churn leaked: {rec.lines}"


def test_deliberating_churn_collapses():
    on_line, rec = pump()
    for s in ["Deliberating… (4s · 148 tokens)",
              "Deliberating… (14s · 440 tokens)",
              "Deliberating… (42s · 880 tokens · esc to interrupt)"]:
        on_line(s)
    assert len(rec.lines) <= 1


def test_real_output_still_gets_through():
    """Filtering must not silence the content the operator actually wants."""
    on_line, rec = pump()
    on_line("Create(/home/u/project/site/public/index.html)")
    on_line("Thought for 3s, 2.7k tokens")
    on_line("Defining the public directory")
    assert len(rec.lines) == 3, rec.lines


def test_real_work_is_never_hidden_by_surrounding_status_churn():
    """Status either side of real output must not swallow the output itself.
    The repeated "thinking" IS correctly dropped — it carries no information
    the operator doesn't already have."""
    on_line, rec = pump()
    on_line("thinking")
    on_line("Create(/home/u/site/styles.css)")
    on_line("thinking")
    assert sum("Create" in ln for ln in rec.lines) == 1, rec.lines
    assert len(rec.lines) == 2, rec.lines


def test_exact_repeats_still_deduped():
    on_line, rec = pump()
    for _ in range(10):
        on_line("Analyzing Site Requirements")
    assert len(rec.lines) == 1


def test_empty_and_ansi_only_lines_are_dropped():
    on_line, rec = pump()
    for junk in ["", "   ", "\x1b[2J", "\x1b[0m"]:
        on_line(junk)
    assert rec.lines == []


def test_flood_is_bounded():
    """End to end: a realistic repaint storm must not produce a wall."""
    on_line, rec = pump()
    for i in range(200):
        on_line(f"{i}thinking")
    for word in ["G", "Ge", "Gen", "Gene", "Gener", "Generating"]:
        on_line(word)
    assert len(rec.lines) <= 3, f"{len(rec.lines)} lines leaked through"
