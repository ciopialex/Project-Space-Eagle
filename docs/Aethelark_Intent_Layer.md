# The intent layer — design, not yet built

Two things route a request today, and neither of them is the eagle.

1. **Tool declarations + prompt precedence.** Fixed 2026-08-09: no two tools
   claim the same request without a stated winner. This is now unambiguous,
   and it is a prerequisite for everything below — a predictor built on top of
   a nine-way collision inherits the collision.
2. **Gemini.** It reads 30 declarations and picks. There is no code that
   inspects the utterance, forms a hypothesis, or scores confidence.

That second one is the gap. The eagle has no representation of what you meant;
it receives a decision already committed as a function call.

---

## Why add a layer at all

Three things are impossible without one, and all three have been asked for:

**Nothing can start early.** Preloading needs a hypothesis *while the user is
still speaking*. Today the first moment anything is known is when the model
emits a tool call — after the 350ms silence window, after the round trip. The
Facebook-prefetch idea in `Aethelark_Responsiveness_Notes.md` has nowhere to
plug in.

**There is no confidence signal.** The model either calls a tool or does not.
Nothing knows whether that was a 95% read or a 40% guess, so nothing can hedge,
confirm, or prepare a fallback. The backchannel design needs exactly this —
"checking" versus "mhm" is a function of *predicted time to answer*.

**Recovery is only post-hoc.** `core/tool_fallback.py` fires after a tool has
already failed. Nothing can notice "this smells web-shaped" and warm the
browser before it is needed.

---

## Shape

A deterministic tier in front of the model, modelled on `core/prompt_reflex.py`
— which solves the same problem for a different input and is the best piece of
architecture in this codebase.

    utterance (partial or complete)
        │
        ├─► intent hypothesis + confidence + extracted slots
        │       ~microseconds, regex/keyword, no inference call
        │
        ├─► IF confident AND side-effect-free  → start the work now
        │       warm browser · resolve domain · pre-navigate · pre-collect
        │
        └─► the model still decides
                narrowed by the hypothesis, so it chooses among 2 tools
                rather than 9

The model is never removed from the loop. It is *narrowed* and *overlapped* —
which is where the speed comes from, not from bypassing it.

### Authority, inverted from prompt_reflex

`prompt_reflex`'s rule is: **may STOP on its own authority, may only GO from an
explicit proven-safe list.** Blocking is cheap and reversible; allowing is not.

The intent layer inverts cleanly:

> **May PRE-WARM on its own authority. May only ACT once the model commits.**

Speculative *navigation* is the feature. Speculative *action* is a bug with a
nice name. Warming a browser, resolving a domain, loading a page and reading a
DOM are all retractable — a wrong guess costs a page load nobody sees. Clicking,
buying, sending and deleting are not, and the consent guard exists precisely
because those are unrecoverable.

---

## What it must not become

- **A second guessing layer.** If its confidence is not calibrated it makes
  routing worse, not better. Which is why the prompt precedence had to be
  fixed first, and why it must be measured rather than assumed.
- **A latency cost.** If the hypothesis takes longer than the thing it
  preloads, it is a net loss. Budget: it must be free at the timescale of the
  350ms silence window.
- **A source of side effects.** Enforced structurally, not by discipline: the
  speculative path should only be able to call functions from an explicit
  allowlist of read-only operations.

---

## Gate before building

**This is gated on one measurement.** The whole payoff is latency, and there is
still no live voice baseline — the trace is wired behind `AETHELARK_TRACE=1`
and nobody has spoken to it with tracing on. The user reported ~0.8s → ~4s;
three real causes were found and fixed by inspection and none of them plausibly
accounts for 5×.

One `[Trace]` line decides whether this is worth building:

| where the time is | what to build |
|---|---|
| `response` (model/network) | the intent layer — prediction overlaps the round trip, big win |
| `spoken` (reply length) | not this. Fix verbosity; prediction buys nothing |
| `audio` (playback path) | not this. Fix the audio path |
| `tool` (execution) | partly — prefetch helps, but fix the tool first |

Building it before that measurement would repeat the mistake this project keeps
making: fixing what was found by reading instead of what was found by measuring.

---

## First slice, when it is time

Not the whole thing. One intent, end to end, measured:

**"anything web-shaped" → warm the browser.** It is the highest-payoff single
prediction (browser cold start ~310ms, and the first `open` on a fresh profile
was 7165ms), it has zero side effects, and it is trivially falsifiable: measure
`to_action` on web requests with the predictor on and off. If it does not move,
the design is wrong and nothing else gets built on it.
