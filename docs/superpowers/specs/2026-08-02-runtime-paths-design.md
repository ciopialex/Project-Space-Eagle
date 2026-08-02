# Runtime Paths — Design

**Created:** 2026-08-02
**Status:** approved, implementing

## Why

`.exe` / `.dmg` / `.AppImage` builds cannot save an API key or a single memory.
A frozen bundle resolves user-data paths from `Path(sys.executable).parent` —
Program Files on Windows, the `.app` interior on macOS — both read-only. The
release workflow exists and will happily produce these broken bundles.

`runtime_paths.py` was written to fix it and is imported by exactly one module
(`core/capability/profile.py`, added today). **37 call sites** still compute
user-data paths independently.

Everything built this session — structural grounding, the actionability layer,
cross-platform hands — reaches zero users until this is fixed.

## The finding that reorders the work

`runtime_paths.py`'s docstring claims *"a git checkout resolves
BASE_DIR == USER_DIR, so the curl install path behaves exactly as before."*

**That is false**, and it is probably why the module sat unwired — it reads as
safe. Measured on the development machine:

    base_dir()   /home/shennyonthebeat/Projects/Space-Eagle
    user_dir()   /home/shennyonthebeat/.local/share/aethelark
    same?        False

`user_dir()` never returns the checkout on any platform. Today's users — every
one of them, since `curl | bash` installs into a checkout — have their API key
at `<repo>/config/api_keys.json` and memories at `<repo>/memory/long_term.json`.

A naive migration therefore **destroys live user data**: the app looks in
`~/.local/share/aethelark`, finds nothing, and drops the user into onboarding
with every memory gone.

The rescue is not a later step. It ships first.

## Design

### 1. Data rescue (lands first)

`runtime_paths.migrate_legacy()` — idempotent, runs before anything reads a
user path. For each user-owned file, if the legacy in-repo copy exists and the
user-dir copy does not, copy it across. Never overwrite; never delete the
original. A user who downgrades keeps working.

Covered: `config/api_keys.json`, `config/google_token.json`,
`memory/long_term.json`, `memory/config_manager` state, `config/capability.json`.

### 2. Fix the false docstring

The claim that base and user dirs coincide in a checkout is deleted and
replaced with what actually happens, so the next reader is not misled the same
way.

### 3. Migrate the call sites

37 sites move to `runtime_paths.api_keys_path()` / `memory_path()` /
`user_file(...)`. One module at a time, full suite green after each.

Three path classes:

| Class | Examples | Behaviour |
|---|---|---|
| Bundled, read-only | `core/prompt.txt`, `assets/`, `web/` | `base_dir()`, never written |
| User-owned, writable | `api_keys.json`, `long_term.json`, logs, `capability.json` | `user_file(..., seed=False)` for secrets |
| Seeded once | default memory | `user_file(...)` — copied out of the bundle on first run |

### 4. A guard test so it stays fixed

Fails if any module computes a user-data path from `Path(__file__)` or
`sys.executable` instead of going through `runtime_paths`. This is the part
that stops the problem regrowing in three months.

## Out of scope

Pushing a release tag. This unblocks the packaged builds; rehearsing one via
`workflow_dispatch` and shipping is a separate decision.

## Risks

- **Data loss** if the rescue is wrong. Mitigated by: rescue lands first, is
  copy-only, is idempotent, and is tested against a simulated legacy install.
- **Breaking the checkout path**, which is the only thing shipping today.
  Mitigated by: the full suite runs on a checkout after every module migrated.
- `AETHELARK_HOME` already exists as an override, so tests can redirect the
  whole profile without touching the real one.
