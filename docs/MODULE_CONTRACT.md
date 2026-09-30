# The module contract

What a module must do to be installed, called, and drawn by Space-Eagle.

This document describes an **interface**, not a status. Every rule below
corresponds to a check in the code, and the code is authoritative:

| the rule                | enforced by                          |
| :---------------------- | :----------------------------------- |
| manifest is well-formed | `core/module_bus/manifest.py`        |
| manifest is consistent  | `core/module_bus/validator.py`       |
| the crossing            | `core/module_bus/bus.py`             |

If this file and those files ever disagree, they are right and this is stale.
Read them.

---

## 1. What a module is

A module is **a binary and a manifest**. Nothing else is required.

    ~/.aethelark/modules/<key>/
        manifest.toml           required
        island/
            template.html       only if the module draws a card
            style.css

Space-Eagle never imports your code and never runs in your process. It resolves
`binary` on `PATH`, runs it as a subprocess, and reads stdout. Write the module
in any language that can print JSON.

Discovery is one directory (`default_manifest_dirs`):
`~/.aethelark/modules/*/manifest.toml`, or `$AETHELARK_MODULES_DIR` when set.
The harness ships no module configuration of its own; `<module> register`
copies the module's manifest and island files there.

A module that fails to load logs which file and which field, and **the other
modules load normally**. The failure mode of discovery is "that module is
missing", never "nothing starts".

---

## 2. The crossing

One call is one subprocess.

    argv from the manifest  ──►  your binary  ──►  stdout
                                                     │
                                              exit code 0 = success

- **Print exactly one JSON object** to stdout when `output = "json"`.
- **Exit non-zero to fail.** stdout and stderr are read back to the user, so
  the message on the way out is the message they hear.
- Declaring `output = "json"` and printing something else is an error the host
  reports as such. It will not guess a structure for you.
- Numbers stay numbers. Emit `5562502934738`, not `"5.6T"` — the card renders,
  the model does arithmetic, and a string can do neither. A field the source
  withheld is `null`, never a dash.

---

## 3. The ceiling — the rule most modules get wrong

**`MAX_OUTPUT_CHARS = 16000`** (`core/module_bus/bus.py`).

Your payload is split in two on the way out:

    your JSON ──┬──► model_view() ──► clipped at 16,000 ──► the language model
                └──► whole, uncut ──────────────────────► the card / the island

The card gets everything. **The model's copy is truncated mid-structure** at
16,000 characters, with a character count appended.

Payloads exceeding the ceiling are truncated mid-structure, causing the model
to reason over incomplete data. When a payload overflows, the bus reports the
dropped character count on stderr. Three ways to stay within budget:

1. **Summarise.** Compute totals and counts over everything, and send those.
   An aggregate stays true no matter how many rows you omit; a truncated list
   does not.
2. **Rank, then cap.** Send the top N by whatever matters, so the important
   row is present by construction rather than by byte offset.
3. **Underscore the bulk** — see below.

---

## 4. The underscore rule

**A key beginning with `_` is stripped from the model's copy at any depth**
(`model_view` in `bus.py`) and kept whole for the card.

Use it for anything the screen needs and a language model cannot read:

    "_series":       {...}   six chart ranges, 487 numbers
    "_transactions": [...]   a full ledger the card can page through
    "_preview":      "..."   a base64 mesh

A measured case: a stock quote is 4,542 characters, of which 4,312 are the
chart. Underscored, the model receives 218 characters and the card still draws
the graph. Without it, every quote spent 4,312 characters on numbers no voice
model can use.

The rule lives at the point the payload is written, which is the only place
that knows what the data is for. There is no manifest field listing UI-only
paths, deliberately — such a list drifts from the payload it describes and
nothing catches it when it does.

---

## 5. The manifest

### Top level

| key            | required | meaning                                       |
| :------------- | :------- | :-------------------------------------------- |
| `key`          | yes      | short module name; prefixes every tool name   |
| `binary`       | yes      | executable resolved on `PATH`                 |
| `description`  | no       | one line about the module                     |
| `output`       | no       | `"json"` or `"text"` (default `"text"`)       |
| `requirements` | no       | Python packages an installer must fetch       |
| `entrypoint`   | no       | `package.module:callable`, for bundled modules|

### `[[tools]]`

| field           | meaning                                                     |
| :-------------- | :---------------------------------------------------------- |
| `name`          | bare name; the host calls it `<key>_<name>`                  |
| `description`   | read by the model at routing time — see §6                   |
| `argv`          | the command, as a list                                       |
| `seconds`       | timeout budget for this tool                                 |
| `reads`         | resources it looks at                                        |
| `writes`        | resources it changes                                         |
| `one_at_a_time` | two calls to this tool must not overlap                      |
| `exclusive`     | nothing else may run beside it                               |
| `danger`        | `safe` \| `undoable` \| `permanent`                          |
| `confirm_prompt`| the question asked aloud before a `permanent` action         |
| `injects`       | argument names the host fills, not the model                 |
| `internal`      | hide from the model; the host may still call it              |

Valid `reads`/`writes` resources — the scheduler arbitrates over these and
nothing else:

    net  web  file  files  printer  desktop  system  memory  camera  island

Naming a resource outside that set declares a hazard nothing checks, which is
worse than declaring none: it reads as protected and is not.

### `[tools.params.<name>]`

| field         | meaning                                       |
| :------------ | :--------------------------------------------- |
| `type`        | `STRING` `INTEGER` `NUMBER` `BOOLEAN` `ARRAY` `OBJECT` |
| `description` | read by the model                              |
| `required`    | missing required args fail before anything spawns |
| `default`     | what the tool does when the arg is omitted; a confirmed call that spells it out is the same request |

**Write every flag as one argv element.**

    argv = ["insider", "{ticker}", "--days={days}", "--json"]     correct
    argv = ["insider", "{ticker}", "--days", "{days}", "--json"]  wrong

An element whose placeholder has no value is **dropped whole**. Split across
two elements, an absent value leaves a bare `--days` behind, which then
swallows whatever argument follows it.

---

## 6. Tool descriptions are read by a small, fast model

The eagle routes with a small model in the middle of a spoken turn. Your
description is not documentation; it is the routing signal.

- **Say when to use it, in the user's words.** `"Use for 'are insiders buying
  X', 'who is selling X'."`
- **Say when not to.** Two tools that both look plausible is a coin flip.
- **Never write a description that matches ordinary conversation.** A tool
  saying `"Use for 'how's it going'"` will be called when the user says hello.
  This has happened, twice, to two different tools.
- **Answer errors with the next step, not just the failure.** That is what
  `ToolResult.guidance` is for on the host side, and your stderr is what fills
  it. "Not found" is a dead end; "call again with the ticker instead of the
  company name" is not.
- **Short beats complete.** See §3.

---

## 7. `[island]` — drawing a card

Optional. A module with tools and no cards is normal and complete.

> See [`docs/APPLE_DYNAMIC_ISLAND_ARCHITECTURE.md`](APPLE_DYNAMIC_ISLAND_ARCHITECTURE.md) for the sovereign host FSM, slot contracts (`leading`, `trailing`, `center`, `bottom`), and Apple HIG standards.

    [island]
    about    = "ticker"          # the payload field that makes two answers ONE card
    first    = "small"           # which card opens
    template = "template.html"   # every card, as stages of one file (the default)
    css      = "style.css"       # a FILE NAME beside the manifest, not CSS text

      [island.cards.small]
      size     = { w = 320, h = 62 }
      shows    = ["ticker", "price", "..."]
      prefetch = ["analyze"]    # tools worth running while this card is read

`shows` is the list of payload fields a card draws. Anything the card renders
belongs in it.

`about` decides whether a second answer refines the card already on screen or
replaces it. It must appear in some card's `shows`.

The template is HTML with `{placeholder}` names matching your payload fields.
The installed copy under `~/.aethelark/modules/<key>/island/` wins at runtime
over any copy vendored elsewhere.

Card names are yours except for five the eagle uses for its own states:
`idle`, `listening`, `thinking`, `speaking`, `none`.

### What the host reads

| declaration        | what it does                                       |
| :----------------- | :------------------------------------------------- |
| `template`, `css`  | loaded and injected                                |
| `about`, `shows`   | decide whether an answer earns the screen          |
| `size`             | sizes the island window, not the card              |
| `prefetch`         | run in the background while the card is on screen  |
| `fresh_for`        | seconds a stored answer stays worth reusing        |
| `dwell`            | seconds a stage stays up untouched, e.g. `{ expanded = 30 }` |
| `live`, `live_param` | a tool the host runs with `{live_param: <about value>, state: on/off}` while the expanded card is up; it answers `{"url": ...}` |

Nothing else in `[island]` is read. How one card leads to the next is the
island's own, the same for every module:

    running activity (compact bar) --tap--> glance --tap--> expanded --tap--> compact bar
    any card left alone decays to the compact bar if something is running, else to rest
    island_view (voice): summary = glance, details = expanded, close = back

A module with no `prefetch` opens its expanded card at once; one with
`prefetch` waits for it. Live media is attached only while the expanded card
is on screen: mark the element `<img data-live>` and style
`.pill[data-live="waiting|on|off"]`; the source is removed when the card goes.

### Opening at a depth: `_stage`

A payload may carry `"_stage": "glance"` or `"expanded"` to open there instead
of where it would land. A status answer about something already running opens
on `glance` (the compact bar already is its capsule); a first-layer or error
alert opens on `expanded`, where the camera is. Underscored, so the model never
reads it.

### Sending finished values: `card`

A payload may carry the exact text for its template's placeholders:

    "card": {"variant": "job", "eta_left": "3h 18m left", "pct_label": "26%", ...}

`_card` is the same, hidden from the model: use it when the answer the model
reads already says everything and the card only repeats it for the screen.

The host binds these as escaped text, sets `data-variant` on the island, and
runs no module-specific formatting. Missing values render empty. Use the variant
in your CSS (`.pill[data-variant="job"] .browse { display: none }`) to keep
several layouts in one template.

### Controls: `data-action`

    <button data-action="light" data-arg-state="on" data-arg-printer="{printer_key}"
            aria-pressed="{light_on}">

A tap runs your tool `light` with the `data-arg-*` values, through the module
bus, like an App Intent on a Live Activity. The host runs it only if your
manifest declares the tool, it is not `danger = "permanent"`, it has no
`confirm`, and only with parameters the tool declares; anything else is
refused. Buttons inside an element with `data-group` behave as a segmented
control; `data-toggle="<arg>"` flips `aria-pressed` and that argument between
`on` and `off` on success. A failure shakes the button and keeps the reason as
its tooltip.

### What the host keeps for itself

Your stylesheet only reaches your card. `:root` variables are re-scoped to the
card, rules aimed at host chrome (`.pill` itself, `#plive`, `#psat`,
`#dwellbar`, `.pmark`, `.stage`, `html`, `body`) are dropped, and your
`@keyframes` are renamed so they cannot replace the host's. Geometry, timing,
the spring and the voice indicators are the island's.

---

## 8. What the validator refuses

A manifest failing any of these does not load, and the reason names the file
and the field. Ordered as `validator.py` reports them — most concrete first.

- `danger = "permanent"` with no `confirm_prompt`. A real-world action needs a
  question asked out loud before it.
- `injects` naming an argument no `argv` entry uses — the host would fill in
  nothing and the tool would run without the thing it was written to act on.
- Cards declared and the `template` missing from every island directory.
- A card named `idle`, `listening`, `thinking`, `speaking` or `none`.
- `prefetch` naming a tool the module does not declare.
- `[island]` with no `first`, or a `first` that is not a declared card.
- `[island]` with no `about`, or an `about` in no card's `shows`.
- A `css` file missing from every island directory.

---

## 9. `[events]` — speaking unprompted

    [events]
    listener = "yourmod listen"
    priority = { print_failed = 90, job_complete = 40 }

A long-lived `listener` process writes your latest event to
`<key>_dynamic_island.json` in the user's private runtime directory
(`$XDG_RUNTIME_DIR`, or the temp directory where there is none) by atomic
temp-file swap; the host reads the file. The listener inherits the host's
environment, so both compute the same path.

**One slot, not a queue.** The island shows one thing and it decays, so the
newest important event belongs on screen rather than a backlog of stale ones.
Rewrite the slot; do not append.

`priority` is how much each event matters — higher wins when two arrive
together. You order your own events; the host owns the bands. An event nobody
has ranked sits in the middle rather than at the top or the bottom.

### Live activities

Something with a beginning and an end (a print, a render, a transfer) is an
activity. Put this on every event while it runs:

    "activity": {"id": "CC2", "state": "active", "leading": "CC2",
                 "trailing": "1h 20m", "progress": 0.42, "relevance": 50,
                 "stale_after_s": 60}

`activity` may be a list, one entry per running thing. While any activity is
live, the resting island shows the most relevant one in its compact form
(leading, a progress ring, trailing) and a second one as a detached bubble.
Updates redraw it in place and never open a card. Send `"state": "ended"` when
it is over; it leaves the island at once. Silence longer than `stale_after_s`
dims it; eight hours ends it.

An update opens your card only when it carries an alert id:

    "alert": "CC2:benchy.gcode:first_layer"

Each id is shown once. Keep repeating it for as long as it is true; the host
reads your slot on its own clock and may miss a single frame. Alert only for
what a person should not miss: finished, paused, failed, first layer down.

---

## 10. Checklist before you ship

- [ ] `binary` is on `PATH` and runs with no arguments without crashing.
- [ ] Every tool prints one JSON object and exits 0.
- [ ] No tool's model-facing payload exceeds 16,000 characters. Test your
      **worst** case, not your typical one — the busy company, the big fleet,
      the long history.
- [ ] Bulk data the card needs and the model cannot read is `_`-prefixed.
- [ ] Every flag in `argv` is a single element.
- [ ] Every `permanent` tool has a `confirm_prompt`.
- [ ] Every description says when to use the tool and when not to, and none of
      them match a greeting.
- [ ] The module loads: start the host and confirm no validator lines name
      your manifest.
