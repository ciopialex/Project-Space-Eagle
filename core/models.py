"""Which Gemini model each job runs on — in one place, not fourteen.

Choices here are probed against the real key, not read off a pricing page
(`scratch/probe_models.py` reproduces it):

  - `gemini-3.7-flash` is the strongest agentic model the free tier reaches,
    but it returned 503 UNAVAILABLE on 2 of 4 back-to-back calls. That is
    capacity, not permission — it succeeds on retry. It is NOT the default,
    because a planner that fails half the time is worse than a slightly
    weaker one that answers. Opt in with AETHELARK_MODEL_DEFAULT.
  - `gemini-3.6-flash` answered every call and is Google's recommended model
    for the computer_use tool. That combination makes it the workhorse.
  - `gemini-3.1-flash-lite` is fast and stable but rejects computer_use
    ("Computer Use is not enabled"), so it must never back a grounding path.
  - `gemini-2.5-pro` is retired — 404 NOT_FOUND on this key.

The live/native-audio models are deliberately absent. Voice runs on a
different API surface (LiveConnect), its latency baseline was tuned against
the model it names, and swapping it is a separate piece of work with its own
measurements. It stays pinned at its call site until then.
"""
from __future__ import annotations

import os

#: Proven-stable workhorse: general reasoning, tool selection, everyday turns.
#: Also the model Google recommends for computer_use, which keeps the
#: perception path and the reasoning path on one model.
DEFAULT = os.environ.get("AETHELARK_MODEL_DEFAULT", "gemini-3.6-flash")

#: Multi-step planning, mission decomposition, code writing — the jobs where
#: a better model most changes the outcome. Same id as DEFAULT today; kept a
#: separate name so raising it to `gemini-3.7-flash` is a one-line change here
#: rather than a hunt through the call sites.
AGENTIC = os.environ.get("AETHELARK_MODEL_AGENTIC", DEFAULT)

#: High-throughput, low-stakes work: summarising a file, classifying an
#: intent, naming a thing. Cheaper and quicker; wrong answers are cheap here.
FAST = os.environ.get("AETHELARK_MODEL_FAST", "gemini-3.5-flash-lite")

#: Anything that looks at pixels and returns a coordinate. MUST be a model
#: that accepts the computer_use tool — see the flash-lite note above.
GROUNDING = os.environ.get("AETHELARK_MODEL_GROUNDING", "gemini-3.6-flash")
