# Packaging Aethelark

Tag-driven installers for Windows, macOS and Linux, published as a GitHub
release. The website reads that release through its `/api/aethelark/release`
endpoint and swaps its download button over on its own within ~5 minutes — no
deploy on the website side.

```
git tag v0.1.0
git push origin v0.1.0
```

That is the whole release process, once the blockers below are cleared.

| File | What it does |
|---|---|
| `../.github/workflows/release.yml` | Builds all three platforms on tag push, publishes the release |
| `aethelark.spec` | PyInstaller spec — data files, hidden imports, the macOS bundle |
| `aethelark.iss` | Inno Setup script → the Windows `.exe` installer |
| `smoke_test.py` | Fails the build if the bundle can't import its own dependencies |
| `runtime_paths.py` | Read-only bundle vs. writable user data (**not yet wired in**) |

Filenames are chosen to match what the website's `_classify_asset` recognises:
`.exe`/`.msi` → Windows, `.dmg`/`.pkg` → macOS,
`.AppImage`/`.deb`/`.rpm`/`.tar.gz` → Linux. Anything else is dropped rather
than guessed, and a release with no recognised installer leaves the site
showing the clone command instead of a dead button.

---

## Blockers — read before tagging

These are real, and the first two will produce installers that look fine and
then lose your data.

### 1. `PyQt6-WebEngine` is missing from `requirements.txt`

`aethelark_web.py` and `web_shell.py` both import
`PyQt6.QtWebEngineWidgets`, but `pip install PyQt6` does not provide it — it is
a separate distribution. A clean checkout cannot start the app today.

The workflow installs it explicitly so builds are not blocked, but the real fix
is one line in `requirements.txt`:

```
PyQt6-WebEngine
```

### 2. Nothing in the codebase knows it might be frozen

`grep -rn "_MEIPASS\|sys.frozen"` returns nothing. Every path is built from
`Path(__file__).parent`, and two of them are written at runtime:

- `config/api_keys.json` — written by `actions/browser_control.py:38`
- `memory/long_term.json` — the whole memory system

In an installed build that directory is read-only (Program Files, or inside
`Aethelark.app`). The writes will either fail outright or, on a onefile build,
land in a temp directory that is deleted on exit — **your API keys and memory
would vanish on every restart**, with no error.

`runtime_paths.py` is the fix, ready to drop in. Move it to the repo root and
replace the two write paths:

```python
from runtime_paths import api_keys_path, memory_path, read_json, write_json
```

It resolves to `%APPDATA%\Aethelark` / `~/Library/Application Support/Aethelark`
/ `~/.local/share/aethelark`, seeds `long_term.json` from the shipped copy on
first run, writes atomically, and honours `AETHELARK_HOME` for portable installs.

I have not wired this in — it touches app logic across several files and you
asked for the CI, not a refactor. It is the one thing standing between this
pipeline and installers that genuinely work.

### 3. Unsigned builds get blocked on Windows and macOS

Out of the box:

- **Windows** — SmartScreen shows "Windows protected your PC" until the
  installer builds reputation. An EV code-signing certificate (~$300/yr)
  removes it immediately.
- **macOS** — Gatekeeper flatly refuses an unsigned, un-notarized app
  downloaded from the web. The user has to right-click → Open, or run
  `xattr -d com.apple.quarantine`. For a real product this needs an Apple
  Developer account ($99/yr), then `codesign` + `notarytool` in the workflow.

Linux AppImages have no such gate.

This is the difference between "it downloads" and world-class. Neither can be
solved in code — both need paid developer identities. Once you have them, the
signing steps slot into `release.yml` behind `if: secrets.X != ''`.

### 4. Bundle size

Expect **250–400 MB** per platform. Qt WebEngine is most of it, opencv and
numpy the rest. Playwright's browsers (~400 MB more) are deliberately *not*
bundled — `runtime_paths.ensure_browsers()` fetches them on first use, since
most sessions never drive a browser.

If that is too big, the lever is Qt WebEngine: the dashboard is rendered from
`web/dashboard.html`, so a future native-widget path would drop ~200 MB.

---

## Building locally

```bash
pip install -r requirements.txt PyQt6-WebEngine pyinstaller==6.11.1
pyinstaller packaging/aethelark.spec --noconfirm --clean
python packaging/smoke_test.py
```

Output lands in `dist/Aethelark/` (`dist/Aethelark.app` on macOS).

Rehearse the full pipeline without burning a tag: **Actions → release → Run
workflow**, which builds all three platforms and uploads them as workflow
artifacts without publishing a release.

## macOS icon

`aethelark.spec` sets `icon=None` for the `.app` because the repo only has
`config/aethelark.ico`, which macOS cannot use. To fix:

```bash
mkdir Aethelark.iconset
sips -z 512 512 assets/images/aethelark.png --out Aethelark.iconset/icon_512x512.png
iconutil -c icns Aethelark.iconset -o packaging/aethelark.icns
```

then point `BUNDLE(icon=...)` at it.
