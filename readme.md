# Aethelark

**A voice-commanded operator layer for your machine — and a constitutional CEO for every AI coding agent.**

You speak. It acts. Locally, on any OS, under laws it cannot break.

---

## The idea

Chatbots produce answers. Aethelark produces **consequences** — it hears you, sees your screen, and works your tools on your behalf. It sits one layer above the operator: the GUI and the terminal become its hands.

It is **provider-independent**. The intelligence is a commodity you plug in — a free model or a frontier one. The value is the circuit the intelligence flows through: the protocol, the memory, and the constitution that keep autonomy safe, reversible, and yours.

---

## What it does

- **Speaks & acts** — real-time voice control of apps, files, the browser, and messaging. Windows · macOS · Linux.
- **Sees** — live screen and camera vision, piped into the conversation.
- **Remembers** — persistent memory that learns who you are and what you're building.
- **Connects** — Google (Gmail, Calendar, Contacts, Tasks) and WhatsApp, briefed and acted on by voice.
- **Commands a swarm** — decomposes a mission, sizes a team of AI coding agents (Claude Code, Antigravity, Codex, and more), and drives them to *done*.

---

## How it works

**Ghost & Kernel.** The Ghost does the labor — it reads terminals, answers agents' questions, presses the keys, and keeps the loop alive as if a careful operator were always present. The Kernel is the law — it records the mission, protects invariants, verifies outcomes, stages a rollback before every mutation, and escalates to you when a decision is truly yours.

**Conductor, not composer.** The brain that runs Aethelark is a conductor. It borrows intelligence from whatever coding tools are installed and orchestrates them: a *Chief Architect* plans, a team executes in isolated git worktrees, a shared blackboard keeps coupled work in sync, a sentinel self-heals stalls, and a reviewer verifies and merges. One agent's caught mistake becomes a lesson the whole swarm inherits.

Capability may be fast, creative, and occasionally wrong. The Kernel's job is to ensure capability never outranks law.

---

## Install

One line. It installs everything — a private Python runtime, all dependencies, and the `eagle` command — then launches.

**macOS**
```bash
curl -fsSL https://get.aethelark.com | bash
```
> Open Terminal with **Cmd + Space** → type `Terminal` → Enter. Paste, press Enter.

**Linux**
```bash
curl -fsSL https://get.aethelark.com | bash
```
> Open a terminal with **Ctrl + Alt + T**. You'll be asked for your password once, to install the Qt system libraries pip can't provide.

**Windows** — *beta*
```powershell
irm https://get.aethelark.com/install.ps1 | iex
```
> Right-click **Start** → **Terminal**. Paste, press Enter.
> Windows support is new and hasn't yet been tested on a real Windows machine — please [open an issue](https://github.com/ciopialex/Project-Space-Eagle/issues) if something breaks.

Nothing needs admin rights (except the Linux system libraries), and nothing touches your system Python. Everything lands in `~/.aethelark`. Expect 5–15 minutes — PyQt6, WebEngine, OpenCV and numpy are a large download.

To uninstall: `rm -rf ~/.aethelark ~/.local/bin/eagle`

---

## Run it

```bash
eagle
```

That's it — from any terminal, any directory, any time. The installer launches it once for you at the end; after that, `eagle` is the whole command.

First run walks you through ignition: pick a brain, connect your accounts, name the eagle. Then talk to it.

---

## Get a free Gemini API key

Aethelark needs a brain. Google gives one away free — no credit card, no trial clock, no billing setup. It takes about a minute.

1. Go to **[aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey)**
2. Sign in with any Google account
3. Click **Create API key**
4. Click **Create API key in new project** if it asks
5. Hit the **copy** icon next to the key
6. Paste it into Aethelark's onboarding screen

Done — you can talk to the eagle.

**What "free" actually means here.** This is Google's permanent free tier, not a trial. You are never asked for a card and you cannot be charged. It's rate-limited rather than dollar-capped — roughly 10 requests per minute and 250 per day on Flash — which one person talking to their computer will rarely notice. If the eagle ever goes quiet after heavy use, you've hit a rate limit, not a bug; it resets on its own.

Already have a key, or running a local model? Onboarding handles both — Aethelark speaks to Ollama and any OpenAI-compatible server (LM Studio, Jan, vLLM) as well as Gemini.

---

## Three ways to open it

The terminal is a one-time thing. After installing, pick whichever you like:

- **Click the icon.** The installer creates a real app — `Aethelark.app` in your Applications on macOS, an entry in the app grid on Linux, Start Menu and Desktop on Windows.
- **Type `eagle`** in any terminal, from any directory.
- **Your own keyboard shortcut.** Bind it once and summon the eagle from anywhere:
  - **Ubuntu / GNOME** — Settings → Keyboard → Custom Shortcuts → command `eagle`
  - **macOS** — Automator → Quick Action → Run Shell Script `eagle`, then assign a key in Settings → Keyboard
  - **Windows** — right-click the Start Menu shortcut → Properties → Shortcut key

Aethelark deliberately ships **no global hotkey listener of its own** — that would mean a background process watching every keystroke. Your OS already does this properly, so we let it.

---

## Privacy

Aethelark is not always listening, and it is not always running. It starts when you open it and stops when you close it. No wake word, no ambient microphone.

**There is no Aethelark server.** Your voice and your screen go from your machine straight to Google, under *your own* API key. They never pass through infrastructure the author controls — because there isn't any.

- **Your memory is a file you own.** It lives at `~/.local/share/aethelark/long_term.json` (`~/Library/Application Support/Aethelark` on macOS, `%APPDATA%\Aethelark` on Windows). Open it, read it, edit it, delete it. Nothing is synced anywhere.
- **The microphone is off until you open the app**, and the pill visibly shows when it is LISTENING or SPEAKING.
- **`[ESC]` stops it mid-action**, whatever it is doing.
- **The code is public.** Every claim above is checkable in this repository — please do check them.

---

## Status

Active development. Voice, vision, memory, connectors, and the swarm foundation are live; the Chief Architect planning layer is landing now. Local-first, one operator, no subscriptions.
