# Contributing to Aethelark

Thank you for testing and improving it. This file is for people and for the AI
coding agents they run.

## Before you send anything

- **The license** is [PolyForm Noncommercial 1.0.0](LICENSE). By opening a pull
  request you confirm that you wrote the change (or may submit it), and you grant
  the maintainer a perpetual, worldwide, royalty-free license to use, modify,
  distribute and relicense it, including commercially. You keep your copyright.
- **Never include a secret or personal data**: no API keys, tokens, key files,
  `memory/` contents, logs or screenshots of your screen. A voice-session log is
  the whole conversation. Enable the guard in your clone:

  ```bash
  git config core.hooksPath .githooks
  ```

  It refuses a commit or push that carries your own credentials, home path,
  hostname or email, or anything shaped like a key.

## Set up

```bash
git clone https://github.com/ciopialex/Project-Space-Eagle.git
cd Project-Space-Eagle
uv venv --python 3.12 .venv          # or: python3.12 -m venv .venv
uv pip install --python .venv/bin/python -r requirements.txt -c constraints.txt pytest
.venv/bin/python -m pytest tests -q -m "not live"
```

On Linux the tests need a display: `xvfb-run -a .venv/bin/python -m pytest tests -q -m "not live"`.
On Windows use `.venv\Scripts\python.exe`.

Run the app from your clone with `.venv/bin/python aethelark_web.py`. It needs a
free Gemini API key, which onboarding walks you through. The installed `eagle`
command is a separate copy; it never touches your clone.

## What a good pull request looks like

- **One change, and why.** Say what was wrong in terms of what the user sees.
- **Run the real thing.** Read the code, run the binary. A test that only asserts
  what the code already says is worth nothing.
- **Tests only where they earn it:** an action that cannot be undone or costs
  something real, or a seam where two systems meet (tool ↔ dispatcher, module ↔
  bus, host ↔ QWebChannel bridge). No tests that search implementation source for
  strings.
- **Text the model reads** (tool descriptions, errors, `guidance`) is read by a
  small, fast model in the middle of a spoken turn. Say what to do next, not only
  what went wrong. No jargon, no exception class names. `ok` is the signal; the
  prose is the fallback. Shorter beats complete. See `CLAUDE.md`.
- **The suite is green** and CI passes. CI also scans your change for secrets.

## Modules

Modules are separate repos and separate processes; the harness ships no module
configuration. A module is written against [`docs/MODULE_CONTRACT.md`](docs/MODULE_CONTRACT.md).
Trade and 3D live in their own repositories:

- https://github.com/ciopialex/Aethelark-Trade
- https://github.com/ciopialex/Aethelark-3D

## Reporting a bug

Open an issue and paste the output of `eagle --doctor`. An installed eagle keeps
no log; to capture one, run `EAGLE_LOG=1 eagle` (on Windows, set `EAGLE_LOG=1`
first), reproduce the problem, and **read the log before posting it**: it contains
what you said and what it answered.

## Security problems

Do not open a public issue. Use GitHub's "Report a vulnerability" on the Security
tab of this repository.
