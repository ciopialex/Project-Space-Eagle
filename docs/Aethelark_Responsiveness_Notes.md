# Perceived responsiveness — design notes (not yet built)

Parked deliberately. Nothing here is implemented. This exists so any session
opened in this repo can pick the thread up without re-deriving it.

Two separate ideas, recorded 2026-08-07. They are related only in that both
attack *perceived* latency rather than actual latency, which is the right
target — measured work already showed the model is not the bottleneck (see
`core/turn_trace.py` and the numbers in `tools/web_bench.py`).

---

## 1. Conversational backchannels and micro-acknowledgements

The goal: the eagle produces short natural turn-taking signals — "mhm",
"right", "got you", "one sec", "checking", "hold on, let me look at that" —
when they improve turn-taking and perceived responsiveness.

Three distinct classes, chosen by what the eagle is *actually* doing:

| class | examples | when |
|---|---|---|
| listener backchannel | "mhm", "right", "yeah" | user is explaining at length; sparse, never interrupting |
| processing acknowledgement | "let me think" | reasoning, no external action |
| action acknowledgement | "checking", "let me pull that up" | a lookup, a browser action, a tool running |

Constraints worth keeping:

- Context-selected, never a canned loading message.
- If the real answer is ready fast enough, **skip the acknowledgement** and
  just answer.
- Vary the phrasing; a fixed string is worse than silence.
- Never imply an action that is not actually happening.
- The filler must never *add* latency — streamed immediately, and
  interruptible the moment the real response arrives.
- One acknowledgement, never a chain of them.
- Well-timed means 150–500ms. Played after a long silence it feels artificial,
  which is worse than having said nothing.
- Adapt to the user's pacing without copying slang, accent, or verbal tics.

### The hard problem: who owns the filler

**The user's objection, which is the real design constraint:** if we build a
backchannel layer *and* the brain behind it is a model that already emits its
own filler in voice mode, the eagle says "checking" twice. GPT-5.5 does this
natively. Gemini Live may or may not, and the brain is swappable — so this is
not a one-time integration detail, it is an architectural decision.

Ownership must be exclusive. Three options:

1. **Client owns it.** We emit the acknowledgement and instruct the model never
   to. Portable across brains, but relies on prompt compliance for suppression,
   and we have already seen prompt-level rules under-deliver (that is why
   `core/tool_fallback.py` is a mechanism rather than prompt advice).
2. **Model owns it.** We emit nothing and let the brain do it. Zero double-talk
   risk, no work — but the behaviour vanishes on a brain that lacks it, and we
   cannot tune timing or class selection.
3. **Capability-declared.** Each brain adapter declares
   `emits_own_backchannel: bool`; the client layer engages only when it is
   False. This is the one that survives a brain swap, and it matches how the
   codebase already handles capability differences elsewhere.

Option 3 is the recommendation. It is also the only one that can be *tested* —
a fake adapter declaring each value proves both paths.

### Measurement, before believing it works

The brief's own standard applies: does the backchannel actually reduce
perceived latency, or does it just add talking? Watchable with the existing
trace — a backchannel should move `to_voice` down sharply while leaving
`to_action` unchanged. If `spoken` grows and `to_action` does not improve, the
feature is costing time and buying nothing. Also worth counting: overlap
events, repeated phrases, and barge-ins during acknowledgements.

---

## 2. Predictive preloading — the Facebook login story

The user's framing, which is the clearest statement of the idea:

> Facebook found that by the time you finish typing your email on the auth
> screen, ~85% of the time you are the owner of that account. So once the email
> matched a real account, they started prefetching that account's front page
> **while you typed your password**. You clicked "log in" and were already
> there. It feels like a miracle of optimisation. It is just a confident guess
> made early.

Applied here: begin the expensive part of the work *while the user is still
speaking or typing*, gated on a confidence estimate, so the visible latency is
whatever remains after the user stops.

What is worth prefetching, roughly in order of payoff:

- **Browser warm-up.** Cold start is ~350ms headless and the profile load is
  the slow part. Starting the browser the moment a request smells web-shaped
  removes it entirely from the critical path. Cheapest and safest — no
  side effects at all.
- **Navigation.** If the utterance so far names a site, `goto` it before the
  sentence ends. Idempotent and reversible.
- **Page snapshot.** `collect()` is 55–62ms even on a huge DOM, so this is
  nearly free once the page is up.
- **Tool routing.** Decide the likely tool early; the confidence matrix the
  user described.

The safety line, and it is not negotiable: **only speculate on operations with
no side effects.** Opening a page, warming a browser, and reading a DOM are
retractable. Clicking, buying, sending, and deleting are not — and the consent
guard exists precisely because those are unrecoverable. Speculative *navigation*
is the feature; speculative *action* is a bug with a nice name.

Wasted work is the acceptable cost. A wrong prefetch costs a page load nobody
sees; a wrong action costs money or data.

---

## Status

Both parked. Current work is the web agency's open items. Neither of these
should start until that is finished, and neither should ship without the
measurement described above — the whole point of the instrumentation already
in the tree is that this project stopped guessing about latency.
