# Voice benchmark — say these, in this order

The point: **if the sentence is fixed, the expected log is known in advance**,
so a diff between expected and actual is a bug report instead of an argument.

```bash
AETHELARK_TRACE=1 eagle 2>&1 | tee /tmp/bench.log
# ...say the lines below, in order, pausing for each reply...
python tools/bench_log.py /tmp/bench.log
```

Leave **~3 seconds of silence** after each line so turns stay separable in the
log. If a line's reply never comes, say "stop", note it, and carry on.

---

## Phase 0 — voice latency, with nothing else in the way

The whole point of these four is that **no tool should fire.** If a `[Tool] ▶`
line appears here, that alone is seconds of latency and it is a routing bug,
not a model-speed problem.

| # | say this | expected |
|---|---|---|
| 1 | "How are you doing?" | no tool. One short sentence. `[Trace]` line present. |
| 2 | "Say the word ready and nothing else." | no tool. `play_q` ≈ **300–600ms**. |
| 3 | "Count from one to five." | no tool. `play_q` ≈ **2000–3000ms**. |
| 4 | "Ce mai faci?" *(Romanian)* | no tool. Reply **in English** — the prompt locks output language. |

**What these isolate.** Lines 2 and 3 differ only in reply *length*. If the
delay you feel is the same on both, it is turn-taking or model time. If line 3
feels much worse, it is playback length and the fix is brevity, not latency.
Line 4 checks the Romanian input path end to end, which is where the
Cloudflare wall bug lived.

## Phase 1 — one tool, local, no network

| # | say this | expected |
|---|---|---|
| 5 | "What's on my desktop?" | `file_controller` **or** `desktop_control`, `✓`, **under 50ms** |
| 6 | "What's my system status?" | `system_status`, `✓` |

Anything over ~200ms here is not the network — it is the tool.

## Phase 2 — one tool, network API

| # | say this | expected |
|---|---|---|
| 7 | "What's my latest liked video?" | `youtube_api` `{action=liked}`, `✓`, ~450ms. Result line must now end with `[link, do not read aloud: https://…]` |

**Regression check.** If that link marker is missing, the id fix did not take.
It must **not** read the URL aloud.

## Phase 3 — the chain that was broken

| # | say this | expected |
|---|---|---|
| 8 | "Summarise my latest liked video." | `youtube_api` → `youtube_video {action=summarize}`. |

**This is the one that used to fail.** Previously: `[Tool] ? youtube_video …
(0ms) — That does not look like a YouTube link`. It should now resolve. A 0ms
failure here means the chain is still broken, and `bench_log.py` will flag it
as *REFUSED ITS OWN INPUT*.

Expect a Gemini 429 sometimes — that is quota, not a bug, and should say so.

## Phase 4 — the bot wall

| # | say this | expected |
|---|---|---|
| 9 | "Open bambulab.com" | `web_agency {action=open}` → **`✗` with a "blocking automated browsers" reason** |

**Regression check.** Previously `✓` with 8 controls of Romanian Cloudflare
interstitial, and the eagle then hunted for a products menu on a challenge
page. `✓` here is a **fail**.

## Phase 5 — web that actually works

| # | say this | expected |
|---|---|---|
| 10 | "Go to en.wikipedia.org/wiki/Motherboard" | `web_agency open`, `✓`, ~600 controls |
| 11 | "What does that page say a motherboard is?" | should answer **from the page** |

**Known weakness.** Line 11 is the open truncation problem — the eagle may
answer from its own knowledge instead of the page, which *looks* like success.
If the answer contains nothing specific to that article, the content never
reached it.

Watch for `REDUNDANT ROUND-TRIP` — a `look` straight after `open` is a wasted
trip, because `open` already returned the controls.

## Phase 6 — the known WhatsApp race

| # | say this | expected |
|---|---|---|
| 12 | "Send Shenny a WhatsApp saying benchmark one." | **expected to FAIL** on the first attempt |
| 13 | "Try again." | expected to succeed, ~12s |

Reproducing the race is the point. From the log: first send dies with
`Target page, context or browser has been closed`, the browser relaunches, and
the second works. Note whether it still costs ~6.5s of "navigate + login"
every time — the browser is not being kept warm.

## Phase 7 — interruption

| # | say this | expected |
|---|---|---|
| 14 | "Tell me everything you know about 3D printing." | a long answer starts |
| 15 | *cut in after ~2s:* "Stop." | `✋ Interrupted (epoch N→N+1)`, playback frames discarded, no tool fires |

## Phase 8 — shutdown

| # | say this | expected |
|---|---|---|
| 16 | Close the window normally | **clean exit** |

**Known bug.** A real session ended `Failed to restore OpenGL context after
clean-up. / Aborted (core dumped)`. If that appears, the crash is reproducible
and worth fixing next.

---

## Reading the result

`tools/bench_log.py` flags these, each one taken from a defect that had
already scrolled past unnoticed in a log:

- **NO STATUS** — a tool gave the model no `ok` flag. It had to guess from prose.
- **REFUSED ITS OWN INPUT** — failed in under 50ms; the argument shape is wrong,
  not the request. Cheap to fix and usually a whole wasted round-trip.
- **REDUNDANT ROUND-TRIP** — `look` right after `open`. Pure latency.
- **REPEATED CALL** — identical args twice; the first answer was not used.
- **CRASH / CONNECTION DROPPED / MIC DROPS**.
- **NO [Trace] LINES** — the run cannot answer the latency question at all.

Each `[Tool] ▶ … ▶ …` pair inside one turn is **a full round-trip to the model
and back**. That is where multi-second turns come from, far more than
inference speed — so a finding that removes a tool call is a latency fix.
