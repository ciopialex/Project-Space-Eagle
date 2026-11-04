#!/usr/bin/env bash
# Aethelark installer — macOS and Linux.
#
#   curl -fsSL https://get.aethelark.com | bash
#
# Installs a private Python runtime via uv (no Homebrew, no Xcode, and no sudo
# except on Linux when a system library is missing), puts the app in
# ~/.aethelark/app, links an `eagle` command onto PATH, and launches it. Modules are added afterwards with
# `eagle install <module>`.
#
# Layout. ~/.aethelark is the eagle's home: installed modules, their
# environments and their data live there. The app itself is one directory
# inside it (app/), a git checkout that an update resets. Keeping them apart is
# what lets an update never touch a module, and a module install never collide
# with the app -- they used to share one directory, and the installer refused
# to run on any machine where a module had been installed first.
#
# Knobs, all optional:
#   AETHELARK_APP=<dir>      where the app goes (default ~/.aethelark/app)
#   AETHELARK_REPO=<url|dir> where to get it from
#   AETHELARK_BRANCH=<name>  which branch (default main)
#   AETHELARK_MODULES=<list> modules to add during the install, comma separated
#                            (default none: the app's first-run screen offers them)
#   AETHELARK_NO_LAUNCH=1    install, but do not start the eagle
#   AETHELARK_SKIP_BROWSER=1 skip the eagle's own browser (about 650 MB)
set -euo pipefail

# One { } group to the last line: bash parses it whole before running any of
# it, so a truncated download runs nothing.
{

REPO="${AETHELARK_REPO:-https://github.com/ciopialex/Project-Space-Eagle.git}"
BRANCH="${AETHELARK_BRANCH:-main}"
EAGLE_HOME="$HOME/.aethelark"
# AETHELARK_HOME named the app directory in earlier installers; still honoured.
HOME_DIR="${AETHELARK_APP:-${AETHELARK_HOME:-$EAGLE_HOME/app}}"
INSTALL_URL="https://get.aethelark.com"
BIN_DIR="$HOME/.local/bin"
PY_VERSION="3.12"
USER_PATH="$PATH"
NEED_MB=3000

# ---------------------------------------------------------------- presentation
ESC=$'\033'; RESET="${ESC}[0m"
SLATE="${ESC}[38;5;245m"; BONE="${ESC}[38;5;255m"; DIM="${ESC}[38;5;240m"
AMBER="${ESC}[38;5;214m"; RED="${ESC}[38;5;203m"
GLOW=("${ESC}[38;5;130m" "${ESC}[38;5;166m" "${ESC}[38;5;208m" \
      "${ESC}[38;5;214m" "${ESC}[38;5;220m" "${ESC}[38;5;229m")

# The emblem, minus the chest cavity the Crest Core is drawn into (rows 5-8).
EAGLE_TOP=(
'`w_                                                  _w'"'"
'  *@g_                                            _g@K'
'    M@@g_                                      _g@@M'
'      M@@@g_             @@@MWmg_            ,@@@M`'
'       ^W@@@@g_         @@@@@@@@@@y       _@@@@W^'
)
EAGLE_MID_L=('       ^w^W@@@@@g_  ' '         Mw^M@@@@@@,' '          ^W@g*W@@@@' '            MW@@,*W@')
EAGLE_MID_R=('   _@@@@@MK,^' '_@@@@@@W*gP' '@@@@WM_@@C' '@WM_@@@M`')
EAGLE_BOT=(
'              "W@@y^W@@@@@@@@@K@@@@@K,@@MM'
'                ^W@W M@@@@@@@@@@@@W`@@WM'
'                  ^M@_^@@@@@@@@@@M_@W^'
'                     W@ M@@@@@@W^,W^'
'                      M@_^@@@@M @M'
'                       ^@y WW^_@M'
'                         WW  g@C'
'                          M@@W`'
'                           MM'
)

CORE_W=14                 # cells inside the bracket glyphs; even keeps it centred
STATE="${TMPDIR:-/tmp}/.aethelark-install.$$"
ANIM_PID=""
LOG="$(mktemp "${TMPDIR:-/tmp}/aethelark-install.XXXXXX")"
INTERACTIVE=""; [ -t 1 ] && INTERACTIVE=1
if [ -z "$INTERACTIVE" ]; then
  RESET=""; SLATE=""; BONE=""; DIM=""; AMBER=""; RED=""
fi

draw() {                  # draw <pct> <phase> <label>
  local pct=$1 phase=$2 label=$3 i line filled bar="" lab
  printf '%s[H%s[J\n' "$ESC" "$ESC"
  for line in "${EAGLE_TOP[@]}"; do printf '  %s%s%s\n' "$SLATE" "$line" "$RESET"; done

  filled=$(( CORE_W * pct / 100 ))
  for ((i = 0; i < CORE_W; i++)); do
    if (( i < filled )); then
      local d=$(( i - phase % (CORE_W + 8) )); d=${d#-}
      if   (( d == 0 )); then bar+="${GLOW[5]}█"
      elif (( d == 1 )); then bar+="${GLOW[4]}█"
      else                    bar+="${GLOW[$((3 + phase / 6 % 2))]}█"; fi
    else bar+="${DIM}░"; fi
  done
  lab=$(printf '%4s' "${pct}%")
  local pad=$(( (CORE_W - 4) / 2 ))
  lab="$(printf '%*s' $pad '')${lab}$(printf '%*s' $(( CORE_W - 4 - pad )) '')"

  printf '  %s%s%s▗%s▖%s%s%s\n'  "$SLATE" "${EAGLE_MID_L[0]}" "$AMBER" \
         "$(printf '▄%.0s' $(seq $CORE_W))" "$RESET$SLATE" "${EAGLE_MID_R[0]}" "$RESET"
  printf '  %s%s%s▐%s%s▌%s%s%s\n' "$SLATE" "${EAGLE_MID_L[1]}" "$AMBER" \
         "$bar" "$AMBER" "$RESET$SLATE" "${EAGLE_MID_R[1]}" "$RESET"
  printf '  %s%s%s▐%s%s%s▌%s%s%s\n' "$SLATE" "${EAGLE_MID_L[2]}" "$AMBER" \
         "$BONE" "$lab" "$AMBER" "$RESET$SLATE" "${EAGLE_MID_R[2]}" "$RESET"
  printf '  %s%s%s▝%s▘%s%s%s\n'  "$SLATE" "${EAGLE_MID_L[3]}" "$AMBER" \
         "$(printf '▀%.0s' $(seq $CORE_W))" "$RESET$SLATE" "${EAGLE_MID_R[3]}" "$RESET"

  for line in "${EAGLE_BOT[@]}"; do printf '  %s%s%s\n' "$SLATE" "$line" "$RESET"; done
  printf '\n   %s%s%s\n' "$SLATE" "$label" "$RESET"
}

animate() {               # background: keeps the core alive during long steps
  local phase=0 pct label
  while :; do
    pct=$(cat "$STATE.pct" 2>/dev/null || echo 0)
    label=$(cat "$STATE.label" 2>/dev/null || echo "Working…")
    draw "$pct" "$phase" "$label"
    phase=$((phase + 1)); sleep 0.08
  done
}

step() {
  printf '%s' "$1" > "$STATE.pct"; printf '%s' "$2" > "$STATE.label"
  [ -n "$INTERACTIVE" ] || printf '  [%3s%%] %s\n' "$1" "$2"
}

cleanup() {
  [ -n "$ANIM_PID" ] && kill "$ANIM_PID" 2>/dev/null || true
  rm -f "$STATE.pct" "$STATE.label" "$STATE.apt.log"
  [ -n "${INTERACTIVE:-}" ] && printf '%s[?25h' "$ESC"
  return 0
}
trap cleanup EXIT INT TERM

die() {
  cleanup
  printf '\n %sInstall failed:%s %s\n' "$RED" "$RESET" "$1" >&2
  if [ -s "$LOG" ]; then
    printf '\n   The last thing it said:\n' >&2
    tail -n 8 "$LOG" | sed 's/^/     /' >&2
    printf '\n   Full log: %s\n   Running the same command again resumes where it stopped.\n' "$LOG" >&2
  fi
  printf '\n' >&2
  exit 1
}

# ---------------------------------------------------------------------- install
OS="$(uname -s)"
[ "$OS" = "Darwin" ] || [ "$OS" = "Linux" ] || die "Unsupported OS: $OS"

# Linux: PyQt6 wheels and the audio library link against system libraries pip
# cannot supply. This runs BEFORE the animation, which redraws the whole screen
# many times a second and would wipe a password prompt. Nothing is asked when
# nothing is missing, which on a desktop that already has a browser and an audio
# player is usually the case.
#
# Package names differ between releases (libasound2 became libasound2t64 in
# Ubuntu 24.04), and a name that only exists as a virtual package passes
# `apt-cache show` and then fails the whole install, so each name is tested for
# an installable candidate, and a failed batch falls back to one at a time.
PKG=""; WANT=""
if [ "$OS" = "Linux" ]; then
  if command -v apt-get >/dev/null 2>&1; then
    PKG=apt
    WANT="libegl1 libgl1 libgbm1 libnss3 libfontconfig1 libdbus-1-3 \
          libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 libxcb-keysyms1 \
          libxcb-shape0 libxcb-image0 libxcb-render-util0 libx11-xcb1 \
          libxcomposite1 libxdamage1 libxrandr2 libxtst6 libxfixes3 libxkbfile1 \
          libportaudio2 git curl gir1.2-atspi-2.0 libcairo2-dev pkg-config build-essential"
    # libasound2t64 replaced libasound2 (Ubuntu 24.04). They are alternatives,
    # not a pair: installing both can conflict, so exactly one is wanted.
    policy="$(apt-cache policy libasound2t64 2>/dev/null || true)"
    case "$policy" in
      *"Candidate: "[!\(]*) WANT="$WANT libasound2t64" ;;
      *)                    WANT="$WANT libasound2" ;;
    esac
    # PyGObject (how the eagle sees an app's buttons) is built from source
    # against whichever girepository generation this release ships.
    if apt-cache show libgirepository-2.0-dev >/dev/null 2>&1; then
      WANT="$WANT libgirepository-2.0-dev"
    else
      WANT="$WANT libgirepository1.0-dev"
    fi
  elif command -v dnf >/dev/null 2>&1; then
    PKG=dnf
    WANT="portaudio libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil nss mesa-libEGL mesa-libgbm libXcomposite libXdamage libXrandr libXtst libXfixes libxkbfile git curl"
  elif command -v pacman >/dev/null 2>&1; then
    PKG=pacman
    WANT="portaudio libxkbcommon-x11 xcb-util-cursor xcb-util-wm xcb-util-keysyms xcb-util-image xcb-util-renderutil nss mesa libxcomposite libxdamage libxrandr libxtst libxfixes libxkbfile git curl"
  fi
fi

syslibs_missing() {
  case "$PKG" in
    # Matched as strings, not through `| grep -q`: under `pipefail` a pipeline
    # whose reader exits early can fail at random when the writer is killed.
    apt) for pkg in $WANT; do
           policy="$(apt-cache policy "$pkg" 2>/dev/null || true)"
           case "$policy" in *"Candidate: "[!\(]*) ;; *) continue ;; esac
           status="$(dpkg-query -W -f='${Status}' "$pkg" 2>/dev/null || true)"
           case "$status" in *"install ok installed"*) ;; *) printf '%s ' "$pkg" ;; esac
         done ;;
    dnf) for pkg in $WANT; do rpm -q "$pkg" >/dev/null 2>&1 || printf '%s ' "$pkg"; done ;;
    pacman) for pkg in $WANT; do pacman -Q "$pkg" >/dev/null 2>&1 || printf '%s ' "$pkg"; done ;;
  esac
}

if [ -n "$PKG" ]; then
  MISSING="$(syslibs_missing)"
  if [ -n "${MISSING// /}" ]; then
    SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
    case "$PKG" in
      apt)    MANUAL="sudo apt-get install -y $MISSING" ;;
      dnf)    MANUAL="sudo dnf install -y $MISSING" ;;
      pacman) MANUAL="sudo pacman -S --needed $MISSING" ;;
    esac
    if [ -n "$SUDO" ] && ! sudo -n true 2>/dev/null; then
      COUNT="$(printf '%s' "$MISSING" | wc -w | tr -d ' ')"
      NOUN="libraries"; [ "$COUNT" = "1" ] && NOUN="library"
      printf '\n  Aethelark needs %s system %s (display, audio and build tools).\n' "$COUNT" "$NOUN"
      printf '  Linux will ask for your password once. Aethelark never sees it.\n\n'
      sudo -v || die "permission to install system libraries was not given.
   Run this once, then run the installer again:
       $MANUAL"
    fi
    printf '  Installing them…\n'
    case "$PKG" in
      apt)
        APT=($SUDO env DEBIAN_FRONTEND=noninteractive apt-get)
        "${APT[@]}" update -qq </dev/null >/dev/null 2>&1 || true
        # shellcheck disable=SC2086
        if ! "${APT[@]}" install -y -qq --no-install-recommends $MISSING </dev/null >"$STATE.apt.log" 2>&1; then
          for pkg in $MISSING; do
            "${APT[@]}" install -y -qq --no-install-recommends "$pkg" </dev/null >>"$STATE.apt.log" 2>&1 || true
          done
        fi ;;
      dnf)    # shellcheck disable=SC2086
              $SUDO dnf install -y -q $MISSING </dev/null >"$STATE.apt.log" 2>&1 || true ;;
      pacman) # shellcheck disable=SC2086
              $SUDO pacman -S --needed --noconfirm $MISSING </dev/null >"$STATE.apt.log" 2>&1 || true ;;
    esac
  fi
fi

FREE_MB="$(df -Pm "$HOME" 2>/dev/null | awk 'NR==2 {print $4}')"
if [ -n "$FREE_MB" ] && [ "$FREE_MB" -lt "$NEED_MB" ] 2>/dev/null; then
  die "Aethelark needs about $((NEED_MB / 1000)) GB of free space and this disk has $((FREE_MB / 1000)) GB.
   Free some space, then run the installer again."
fi

step 0 "Starting…"
if [ -n "$INTERACTIVE" ]; then
  printf '%s[?25l' "$ESC"
  animate & ANIM_PID=$!
fi

step 12 "Fetching the runtime…"
if ! command -v uv >/dev/null 2>&1; then
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL https://astral.sh/uv/install.sh | sh >>"$LOG" 2>&1 \
      || die "could not install uv (needed to provide Python)"
  else
    wget -qO- https://astral.sh/uv/install.sh | sh >>"$LOG" 2>&1 \
      || die "could not install uv (needed to provide Python)"
  fi
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH"

step 24 "Installing Python ${PY_VERSION}…"
uv python install "$PY_VERSION" >>"$LOG" 2>&1 || die "could not install Python ${PY_VERSION}"

step 34 "Downloading Aethelark…"
if ! git --version >/dev/null 2>&1; then
  if [ "$OS" = "Darwin" ]; then
    die "macOS needs Apple's Command Line Tools to download Aethelark (they provide git).
   A window offering them may have just opened. If not, run:
       xcode-select --install
   When that finishes, run the installer again."
  fi
  die "git is not installed. Install it with your package manager, then run the installer again."
fi
mkdir -p "$(dirname "$HOME_DIR")"
# Modules keep account details here (a brokerage login, a printer's access
# code). Owner-only, like the data directory.
chmod 700 "$EAGLE_HOME" 2>/dev/null || true
if [ -d "$HOME_DIR/.git" ]; then
  # Only ever update a checkout that is actually ours. Resetting --hard inside
  # someone else's repository would destroy their uncommitted work.
  existing_remote="$(git -C "$HOME_DIR" remote get-url origin 2>/dev/null || echo '')"
  case "$existing_remote" in
    *Project-Space-Eagle*|*Space-Eagle*)
      if git -C "$HOME_DIR" fetch --quiet --depth 1 origin "$BRANCH" >>"$LOG" 2>&1; then
        git -C "$HOME_DIR" reset --hard --quiet FETCH_HEAD >>"$LOG" 2>&1 || true
      else
        UPDATE_SKIPPED=1
      fi
      ;;
    *)
      die "$HOME_DIR is a git repository, but not Aethelark's ($existing_remote).
   Refusing to touch it. Install elsewhere with:
       AETHELARK_APP=~/aethelark-app curl -fsSL $INSTALL_URL | bash"
      ;;
  esac
elif [ -e "$HOME_DIR" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
  # Never destroy a directory we did not create.
  die "$HOME_DIR already exists and is not empty, and is not an Aethelark
   checkout. Refusing to delete it. Move it aside, or install elsewhere:
       AETHELARK_APP=~/aethelark-app curl -fsSL $INSTALL_URL | bash"
else
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$HOME_DIR" >>"$LOG" 2>&1 \
    || die "could not download Aethelark from $REPO"
fi

# This checkout is the installer's to reset on update (`eagle update` does
# nothing destructive to a checkout without this). Untracked and ignored.
touch "$HOME_DIR/.aethelark-install"

step 46 "Building the environment…"
# Only when there is none. Running the installer again is how an install is
# updated, and uv refuses to create a venv over an existing one -- which made
# every update fail right here.
if [ ! -x "$HOME_DIR/.venv/bin/python" ]; then
  uv venv --python "$PY_VERSION" "$HOME_DIR/.venv" >>"$LOG" 2>&1 \
    || die "could not create the virtual environment"
fi

step 58 "Installing dependencies (this is the long one)…"
VENV_PY="$HOME_DIR/.venv/bin/python"
CONSTRAINTS=()
[ -f "$HOME_DIR/constraints.txt" ] && CONSTRAINTS=(-c "$HOME_DIR/constraints.txt")
# ${arr[@]+...}: macOS runs this under bash 3.2, where expanding an empty
# array with `set -u` aborts the script.
uv pip install --python "$VENV_PY" -q -r "$HOME_DIR/requirements.txt" ${CONSTRAINTS[@]+"${CONSTRAINTS[@]}"} \
  >>"$LOG" 2>&1 || die "the Python packages Aethelark needs did not install."

if [ "$OS" = "Linux" ]; then
  # pyautogui pulls in python3-xlib 0.15, which cannot read the auth cookies
  # GNOME on Wayland writes for Xwayland: every screen tool then fails on the
  # default Ubuntu 24.04/26.04 session. python-xlib is its maintained fork and
  # installs the same `Xlib` package, so writing it over the top replaces the
  # code while pip still sees pyautogui's requirement as met.
  uv pip install --python "$VENV_PY" -q --reinstall-package python-xlib python-xlib \
    ${CONSTRAINTS[@]+"${CONSTRAINTS[@]}"} >>"$LOG" 2>&1 || true
  step 72 "Adding screen understanding…"
  # Not fatal: without it the eagle still works, by sight alone, and
  # `eagle --doctor` says so and what to install.
  uv pip install --python "$VENV_PY" -q PyGObject >>"$LOG" 2>&1 || true
  # GNOME only publishes an app's buttons to that interface when this is on.
  command -v gsettings >/dev/null 2>&1 \
    && gsettings set org.gnome.desktop.interface toolkit-accessibility true \
       >/dev/null 2>&1 || true
fi

step 78 "Verifying…"
if ! IMPORT_ERR="$("$VENV_PY" -c 'import PyQt6.QtWebEngineWidgets, google.genai' 2>&1)"; then
  MISSING_SO="$(printf '%s' "$IMPORT_ERR" | sed -n 's/.*\(lib[A-Za-z0-9_+.-]*\.so[0-9.]*\): cannot open shared object file.*/\1/p' | head -n 1)"
  if [ -n "$MISSING_SO" ]; then
    case "$PKG" in
      apt) HINT="   Find the package that has it:  apt-file search $MISSING_SO" ;;
      dnf) HINT="   Install it with:  sudo dnf install '*/$MISSING_SO'" ;;
      pacman) HINT="   Find the package that has it:  pacman -F $MISSING_SO" ;;
      *) HINT="   Install the package that provides it with your package manager." ;;
    esac
    die "a system library the window needs is missing: $MISSING_SO
$HINT
   Then run the installer again."
  fi
  die "the install is missing critical components:
$(printf '%s' "$IMPORT_ERR" | tail -n 6)
$( [ -f "$STATE.apt.log" ] && { echo '   System libraries said:'; tail -n 6 "$STATE.apt.log"; } )"
fi
# The voice needs PortAudio from the system. Without it the app would open and
# never hear anything, so this is the moment to say so.
"$VENV_PY" -c 'import sounddevice' >/dev/null 2>&1 \
  || die "audio is not available: the PortAudio library is missing.
   On Debian/Ubuntu:  sudo apt install libportaudio2
   Then run this installer again."

if [ "${AETHELARK_SKIP_BROWSER:-}" != "1" ]; then
  step 82 "Fetching the eagle's own browser…"
  # web_agency drives a private browser, separate from the user's own. Not
  # fatal: everything else works without it, and `eagle --doctor` says so.
  "$VENV_PY" -m playwright install chromium >>"$LOG" 2>&1 || true
fi

# Echo cancellation is not applied here. It rewrites the system's default
# microphone and speakers for every application, which is `eagle --doctor
# --fix` territory -- a change the user asks for, not one an installer makes.
# (This step used to run from the wrong directory and silently do nothing.)

step 90 "Linking the \`eagle\` command…"
mkdir -p "$BIN_DIR"
# The launcher is a tracked file, not a heredoc. It stops an older eagle that
# still holds the previous code in memory, and refreshes the module manifests
# and card templates under ~/.aethelark/modules — the copies the bus reads
# FIRST, so a fix to the repo's copy alone would not reach the running app.
# Writing it inline here meant it could only ever be the trivial version.
if [ -f "$HOME_DIR/packaging/eagle" ]; then
  sed "s#@AETHELARK_HOME@#$HOME_DIR#g" "$HOME_DIR/packaging/eagle" > "$BIN_DIR/eagle"
else
  # A checkout old enough to predate packaging/eagle still gets a working command.
  cat > "$BIN_DIR/eagle" <<LAUNCHER
#!/usr/bin/env bash
cd "$HOME_DIR"
exec "$HOME_DIR/.venv/bin/python" aethelark_web.py "\$@"
LAUNCHER
fi
chmod +x "$BIN_DIR/eagle"

# macOS does not put ~/.local/bin on PATH; Linux usually does. Append once.
NEW_TERMINAL=""
case ":$USER_PATH:" in
  *":$BIN_DIR:"*) ;;
  *) NEW_TERMINAL=1
     [ "$OS" = "Darwin" ] && [ ! -e "$HOME/.zshrc" ] && touch "$HOME/.zshrc"
     for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
       [ -f "$rc" ] && ! grep -q '.local/bin' "$rc" 2>/dev/null \
         && printf '\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
     done ;;
esac

step 95 "Creating the app icon…"
# Gatekeeper and SmartScreen only inspect files that were *downloaded* — they
# check a quarantine flag the browser attaches. A launcher we build here, on
# the user's own machine, never carries it, so this is a real double-clickable
# icon with no certificate and no signing involved.
if [ "$OS" = "Darwin" ]; then
  APP="$HOME/Applications/Aethelark.app"
  mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources"
  cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Aethelark</string>
  <key>CFBundleDisplayName</key><string>Aethelark</string>
  <key>CFBundleIdentifier</key><string>com.aethelark.app</string>
  <key>CFBundleExecutable</key><string>Aethelark</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>0.1.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>NSMicrophoneUsageDescription</key><string>Aethelark listens only while you are talking to it.</string>
  <key>NSCameraUsageDescription</key><string>Aethelark uses the camera only when you ask it to look.</string>
</dict></plist>
PLIST
  # A shell script, not a Mach-O binary — so it needs no signature even on arm64.
  # Through the eagle launcher, like every other way of starting it, so a
  # Dock launch gets the same log file and stops an older eagle first.
  printf '#!/bin/sh\nexec "%s/eagle" "$@"\n' "$BIN_DIR" > "$APP/Contents/MacOS/Aethelark"
  chmod +x "$APP/Contents/MacOS/Aethelark"
else
  APPS="$HOME/.local/share/applications"
  mkdir -p "$APPS"
  cat > "$APPS/aethelark.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=Aethelark
Comment=Voice-commanded operator for your machine
Exec=$BIN_DIR/eagle
Icon=$HOME_DIR/assets/images/app_icon.png
Terminal=false
StartupWMClass=aethelark
Categories=Utility;Development;
DESKTOP
  command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS" >/dev/null 2>&1 || true

  # Also place double-clickable shortcut directly on user Desktop.
  if [ -d "$HOME/Desktop" ]; then
    cp "$APPS/aethelark.desktop" "$HOME/Desktop/Aethelark.desktop" 2>/dev/null || true
    chmod +x "$HOME/Desktop/Aethelark.desktop" 2>/dev/null || true
    # Executable is not enough. GNOME shows an untrusted .desktop as a text
    # file and double-clicking it opens a warning rather than the program, so
    # the shortcut has to be marked trusted or it is decorative.
    command -v gio >/dev/null 2>&1 \
      && gio set "$HOME/Desktop/Aethelark.desktop" \
           metadata::trusted true >/dev/null 2>&1 || true
  fi
fi

# Modules are chosen on the app's first-run screen, which lists whatever the
# catalog offers. Naming some here adds them during the install instead, for a
# script or CI; `bundle` leaves alone any module the user removed.
if [ -n "${AETHELARK_MODULES:-}" ] && [ "${AETHELARK_MODULES}" != "none" ]; then
  step 96 "Adding the modules…"
  # shellcheck disable=SC2046
  "$BIN_DIR/eagle" bundle $(printf '%s' "$AETHELARK_MODULES" | tr ',' ' ') >/dev/null 2>&1 || true
fi

step 100 "Ready."
[ -n "$INTERACTIVE" ] && sleep 1.2
cleanup; ANIM_PID=""

cat <<BANNER

   ${BONE}Aethelark is installed.${RESET}

   ${SLATE}Next time, just type${RESET} ${AMBER}eagle${RESET} ${SLATE}in any terminal, or open it from your apps.${RESET}
   ${SLATE}Choose what it can do on first run, or any time in Settings → Modules.${RESET}
   ${DIM}You'll need a free Gemini API key — the app walks you through it.${RESET}

BANNER
if [ -n "$NEW_TERMINAL" ]; then
  printf '   %sOpen a new terminal first: this one does not know the eagle command yet.%s\n\n' "$SLATE" "$RESET"
fi
if [ -n "${UPDATE_SKIPPED:-}" ]; then
  printf '   %sGitHub could not be reached, so the app was not updated; the copy already here was kept.%s\n\n' "$AMBER" "$RESET"
fi

if [ "${AETHELARK_NO_LAUNCH:-}" = "1" ]; then
  exit 0
fi
if [ "$OS" = "Linux" ] && [ -z "${DISPLAY:-}" ] && [ -z "${WAYLAND_DISPLAY:-}" ]; then
  printf '   %sThere is no screen here, so it was not opened. Open Aethelark from your desktop.%s\n\n' "$SLATE" "$RESET"
  exit 0
fi
exec "$BIN_DIR/eagle"

}
