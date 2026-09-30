#!/usr/bin/env bash
# Aethelark installer — macOS and Linux.
#
#   curl -fsSL https://get.aethelark.com | bash
#
# Installs a private Python runtime via uv (no Homebrew, no Xcode, no sudo
# except for Linux system libraries), puts the app in ~/.aethelark/app, links an
# `eagle` command onto PATH, and launches it. Modules are added afterwards with
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
#   AETHELARK_MODULES=<list> modules to add, comma separated (default trade; none skips)
#   AETHELARK_NO_LAUNCH=1    install, but do not start the eagle
#   AETHELARK_SKIP_BROWSER=1 skip the eagle's own browser (~150 MB)
set -euo pipefail

REPO="${AETHELARK_REPO:-https://github.com/ciopialex/Project-Space-Eagle.git}"
BRANCH="${AETHELARK_BRANCH:-main}"
EAGLE_HOME="$HOME/.aethelark"
# AETHELARK_HOME named the app directory in earlier installers; still honoured.
HOME_DIR="${AETHELARK_APP:-${AETHELARK_HOME:-$EAGLE_HOME/app}}"
INSTALL_URL="https://get.aethelark.com"
BIN_DIR="$HOME/.local/bin"
PY_VERSION="3.12"

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

step() { printf '%s' "$1" > "$STATE.pct"; printf '%s' "$2" > "$STATE.label"; }

cleanup() {
  [ -n "$ANIM_PID" ] && kill "$ANIM_PID" 2>/dev/null || true
  rm -f "$STATE.pct" "$STATE.label" "$STATE.apt.log"
  printf '%s[?25h' "$ESC"
}
trap cleanup EXIT INT TERM

die() { cleanup; printf '\n %sInstall failed:%s %s\n\n' "$RED" "$RESET" "$1" >&2; exit 1; }

# ---------------------------------------------------------------------- install
OS="$(uname -s)"
[ "$OS" = "Darwin" ] || [ "$OS" = "Linux" ] || die "Unsupported OS: $OS"

step 0 "Starting…"
printf '%s[?25l' "$ESC"
animate & ANIM_PID=$!

# Linux: PyQt6 wheels and the audio library link against system libraries pip
# cannot supply. Package names differ between releases (libasound2 became
# libasound2t64 in Ubuntu 24.04), and apt refuses an entire install when one
# name is unknown -- which, behind `|| true`, used to mean none of these were
# installed at all. So only names this system actually has are requested.
if [ "$OS" = "Linux" ]; then
  step 4 "Installing system libraries (may ask for your password)…"
  if command -v apt-get >/dev/null 2>&1; then
    sudo apt-get update -qq >/dev/null 2>&1 || true
    WANT="libegl1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
          libxcb-keysyms1 libxcb-shape0 libgl1 libportaudio2 libasound2t64 \
          libasound2 git curl gir1.2-atspi-2.0 libcairo2-dev pkg-config \
          build-essential"
    # PyGObject (how the eagle sees an app's buttons) is built from source
    # against whichever girepository generation this release ships.
    if apt-cache show libgirepository-2.0-dev >/dev/null 2>&1; then
      WANT="$WANT libgirepository-2.0-dev"
    else
      WANT="$WANT libgirepository1.0-dev"
    fi
    # A name that only exists as a virtual package (libasound2 on Ubuntu 24.04)
    # passes `apt-cache show` and then fails the whole install, so the test is
    # for an installable candidate, not for the name.
    HAVE=""
    for pkg in $WANT; do
      if apt-cache policy "$pkg" 2>/dev/null | grep -qE 'Candidate: [^(]'; then HAVE="$HAVE $pkg"; fi
    done
    # shellcheck disable=SC2086
    if ! sudo apt-get install -y -qq --no-install-recommends $HAVE >"$STATE.apt.log" 2>&1; then
      # One package that cannot install must not cost the rest.
      for pkg in $HAVE; do
        sudo apt-get install -y -qq --no-install-recommends "$pkg" >>"$STATE.apt.log" 2>&1 || true
      done
    fi
  elif command -v dnf >/dev/null 2>&1; then
    sudo dnf install -y -q portaudio libxkbcommon-x11 xcb-util-cursor \
      xcb-util-wm xcb-util-keysyms nss mesa-libEGL git curl >/dev/null 2>&1 || true
  elif command -v pacman >/dev/null 2>&1; then
    sudo pacman -S --needed --noconfirm portaudio libxkbcommon-x11 \
      xcb-util-cursor xcb-util-wm xcb-util-keysyms nss git curl >/dev/null 2>&1 || true
  fi
fi

step 12 "Fetching the runtime…"
if ! command -v uv >/dev/null 2>&1; then
  curl -fsSL https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
    || die "could not install uv (needed to provide Python)"
fi
export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
command -v uv >/dev/null 2>&1 || die "uv installed but is not on PATH"

step 24 "Installing Python ${PY_VERSION}…"
uv python install "$PY_VERSION" >/dev/null 2>&1 || die "could not install Python ${PY_VERSION}"

step 34 "Downloading Aethelark…"
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
      git -C "$HOME_DIR" fetch --quiet --depth 1 origin "$BRANCH" 2>/dev/null || true
      git -C "$HOME_DIR" reset --hard --quiet FETCH_HEAD 2>/dev/null || true
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
  git clone --quiet --depth 1 --branch "$BRANCH" "$REPO" "$HOME_DIR" \
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
  uv venv --python "$PY_VERSION" "$HOME_DIR/.venv" >/dev/null 2>&1 \
    || die "could not create the virtual environment"
fi

step 58 "Installing dependencies (this is the long one)…"
VENV_PY="$HOME_DIR/.venv/bin/python"
CONSTRAINTS=()
[ -f "$HOME_DIR/constraints.txt" ] && CONSTRAINTS=(-c "$HOME_DIR/constraints.txt")
# ${arr[@]+...}: macOS runs this under bash 3.2, where expanding an empty
# array with `set -u` aborts the script.
uv pip install --python "$VENV_PY" -q -r "$HOME_DIR/requirements.txt" ${CONSTRAINTS[@]+"${CONSTRAINTS[@]}"} \
  >/dev/null 2>&1 || die "dependency install failed"

if [ "$OS" = "Linux" ]; then
  step 72 "Adding screen understanding…"
  # Not fatal: without it the eagle still works, by sight alone, and
  # `eagle --doctor` says so and what to install.
  uv pip install --python "$VENV_PY" -q PyGObject >/dev/null 2>&1 || true
  # GNOME only publishes an app's buttons to that interface when this is on.
  command -v gsettings >/dev/null 2>&1 \
    && gsettings set org.gnome.desktop.interface toolkit-accessibility true \
       >/dev/null 2>&1 || true
fi

step 78 "Verifying…"
if ! IMPORT_ERR="$("$VENV_PY" -c 'import PyQt6.QtWebEngineWidgets, google.genai' 2>&1)"; then
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
  "$VENV_PY" -m playwright install chromium >/dev/null 2>&1 || true
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
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) for rc in "$HOME/.zshrc" "$HOME/.bashrc"; do
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
Icon=$HOME_DIR/assets/images/aethelark.png
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

if [ "${AETHELARK_MODULES:-trade}" != "none" ]; then
  step 96 "Adding the modules…"
  # Not fatal: the eagle works without them and Settings can add them later.
  # `bundle` leaves alone any module the user removed, so re-running this
  # installer to update never puts one back.
  # shellcheck disable=SC2046
  "$BIN_DIR/eagle" bundle $(printf '%s' "${AETHELARK_MODULES:-trade}" | tr ',' ' ') >/dev/null 2>&1 || true
fi

step 100 "Ready."
sleep 1.2
cleanup; ANIM_PID=""

cat <<BANNER

   ${BONE}Aethelark is installed.${RESET}

   ${SLATE}Next time, just type${RESET} ${AMBER}eagle${RESET} ${SLATE}in any terminal, or open it from your apps.${RESET}
   ${SLATE}Add a module:${RESET} ${AMBER}eagle install trade${RESET} ${SLATE}(stocks) or${RESET} ${AMBER}eagle install 3d${RESET} ${SLATE}(3D printing).${RESET}
   ${DIM}You'll need a free Gemini API key — the app walks you through it.${RESET}

BANNER

if [ "${AETHELARK_NO_LAUNCH:-}" = "1" ]; then
  exit 0
fi
exec "$BIN_DIR/eagle"
