# Aethelark

A voice assistant for your computer. It lives in a small bar at the top of your
screen. Talk to it and it opens apps, works with your files and browser, reads
and sends messages, checks your mail and calendar, and looks at your screen or
camera when you ask. It thinks with Google's Gemini, using your own free API key.

## Install

**macOS and Linux** (in a terminal):

```bash
curl -fsSL https://get.aethelark.com | bash
```

**Windows** (in PowerShell):

```powershell
irm https://get.aethelark.com/install.ps1 | iex
```

It takes 5–15 minutes and needs no admin rights (Linux asks for your password
once, to install system libraries). You don't need Python: the installer brings
its own. When it finishes, the eagle opens and asks for a free Gemini key, your
name, a microphone check, and which modules you want.

You get an app icon (Desktop and Start Menu on Windows, app menu and Desktop on
Linux, Applications on macOS) and an `eagle` command in any terminal.

**A free Gemini key:** go to [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey),
sign in, choose *Create API key*, and paste it into the eagle's setup. Google's
free tier is rate-limited, not billed; if the eagle goes quiet after heavy use,
that is the limit, and it resets.

## Use

Click the icon or type `eagle`, then talk. `Esc` stops it mid-action.

To keep it one keystroke away, bind `eagle` to a shortcut in your OS settings.
Aethelark installs no global hotkey listener of its own.

## Modules

The eagle's abilities beyond the basics are modules, separate programs it calls:

| Module | What it does |
|---|---|
| [Aethelark-Trade](https://github.com/ciopialex/Aethelark-Trade) | Stocks and companies from SEC filings: prices, analysis, insiders, owners. |
| [Aethelark-3D](https://github.com/ciopialex/Aethelark-3D) | Elegoo 3D printers: find models, slice, print, watch. |

You choose which to add on first run (none are required), and can add or remove one any time in **Settings → Modules**. Every module adds tools the model
has to choose between, so remove the ones you don't use. From a terminal:
`eagle install <module>`, `eagle remove <module>`, `eagle modules`.

Anyone can write a module: see [docs/MODULE_CONTRACT.md](docs/MODULE_CONTRACT.md).

## Update and uninstall

`eagle update` updates the app and its modules. The eagle tells you when a newer
version is ready.

To uninstall: on Windows, Settings → Apps → Aethelark. On macOS and Linux:

```bash
rm -rf ~/.aethelark ~/.local/bin/eagle
rm -rf ~/Applications/Aethelark.app                                             # macOS
rm -f ~/.local/share/applications/aethelark.desktop ~/Desktop/Aethelark.desktop # Linux
```

Your keys and memory are kept in a separate folder (below), so removing the app
doesn't erase your setup.

## Privacy

- **Your voice, and your screen or camera when you ask it to look, go to Google's
  Gemini API under your own key.** There is no Aethelark server.
- **Keys and memory stay on your computer**, in `~/.local/share/aethelark`,
  `~/Library/Application Support/Aethelark` or `%LOCALAPPDATA%\Aethelark`. They
  are readable only by you. Memory is a plain file you can open and delete.
- **It listens only while it is running.** There is no wake word. The bar shows
  when it is listening or speaking.
- **Anything that speaks, pops up or listens unprompted is off until you turn it
  on.** The phone remote is off by default.
- An installed copy keeps no log files.
- Tools that need the internet contact those services directly (Google, YouTube,
  weather, web search). Installing and updating fetch from GitHub.

## Status

Beta. Linux is tested on real hardware. macOS and Windows install and start in
automated tests on clean machines, and have had little real-world use, so expect
rough edges there. Please [report bugs](../../issues) with the output of
`eagle --doctor`.

## Contributing, security, license

[CONTRIBUTING.md](CONTRIBUTING.md) · [SECURITY.md](SECURITY.md) ·
[PolyForm Noncommercial 1.0.0](LICENSE): free for noncommercial use, with credit;
commercial use needs a separate license. Copyright (c) 2025-2026
Alexandru-Mihai Cioponea.
