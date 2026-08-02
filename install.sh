#!/usr/bin/env bash
# Aethelark installer — macOS and Linux.
#
#   curl -fsSL https://get.aethelark.com | bash
#
# Installs a private Python runtime via uv (no Homebrew, no Xcode, no sudo
# except for Linux Qt system libraries), clones the app into ~/.aethelark,
# links an `eagle` command onto PATH, and launches it.
set -euo pipefail

REPO="${AETHELARK_REPO:-https://github.com/ciopialex/Project-Space-Eagle.git}"
HOME_DIR="${AETHELARK_HOME:-$HOME/.aethelark}"
INSTALL_URL="https://raw.githubusercontent.com/ciopialex/Project-Space-Eagle/main/install.sh"
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
  rm -f "$STATE.pct" "$STATE.label"
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

# Linux: PyQt6 wheels link against system libraries pip cannot supply.
if [ "$OS" = "Linux" ] && command -v apt-get >/dev/null 2>&1; then
  step 4 "Installing system libraries (may ask for your password)…"
  sudo apt-get update -qq >/dev/null 2>&1 || true
  sudo apt-get install -y -qq --no-install-recommends \
    libegl1 libnss3 libxkbcommon-x11-0 libxcb-cursor0 libxcb-icccm4 \
    libxcb-keysyms1 libxcb-shape0 libasound2t64 libgl1 git curl \
    >/dev/null 2>&1 || true
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
if [ -d "$HOME_DIR/.git" ]; then
  # Only ever update a checkout that is actually ours. Resetting --hard inside
  # someone else's repository would destroy their uncommitted work.
  existing_remote="$(git -C "$HOME_DIR" remote get-url origin 2>/dev/null || echo '')"
  case "$existing_remote" in
    *Project-Space-Eagle*|*Space-Eagle*)
      git -C "$HOME_DIR" fetch --quiet --depth 1 origin main 2>/dev/null || true
      git -C "$HOME_DIR" reset --hard --quiet origin/main 2>/dev/null || true
      ;;
    *)
      die "$HOME_DIR is a git repository, but not Aethelark's ($existing_remote).
   Refusing to touch it. Install elsewhere with:
       AETHELARK_HOME=~/aethelark curl -fsSL $INSTALL_URL | bash"
      ;;
  esac
elif [ -e "$HOME_DIR" ] && [ -n "$(ls -A "$HOME_DIR" 2>/dev/null)" ]; then
  # This used to be `rm -rf "$HOME_DIR"`. AETHELARK_HOME is user-settable, and
  # the default ~/.aethelark is a name other tools use too, so that could
  # silently delete unrelated data. Never destroy a directory we did not create.
  die "$HOME_DIR already exists and is not empty, and is not an Aethelark
   checkout. Refusing to delete it. Move it aside, or install elsewhere:
       AETHELARK_HOME=~/aethelark curl -fsSL $INSTALL_URL | bash"
else
  git clone --quiet --depth 1 "$REPO" "$HOME_DIR" || die "could not download Aethelark"
fi

step 46 "Building the environment…"
uv venv --python "$PY_VERSION" "$HOME_DIR/.venv" >/dev/null 2>&1 \
  || die "could not create the virtual environment"

step 58 "Installing dependencies (this is the long one)…"
VENV_PY="$HOME_DIR/.venv/bin/python"
uv pip install --python "$VENV_PY" -q -r "$HOME_DIR/requirements.txt" \
  >/dev/null 2>&1 || die "dependency install failed"

step 82 "Verifying…"
"$VENV_PY" -c 'import PyQt6.QtWebEngineWidgets, google.genai' >/dev/null 2>&1 \
  || die "the install is missing critical components"

step 90 "Linking the \`eagle\` command…"
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/eagle" <<LAUNCHER
#!/usr/bin/env bash
# Aethelark launcher — written by the installer.
cd "$HOME_DIR"
exec "$HOME_DIR/.venv/bin/python" aethelark_web.py "\$@"
LAUNCHER
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
  printf '#!/bin/sh\nexec "%s/.venv/bin/python" "%s/aethelark_web.py" "$@"\n' \
    "$HOME_DIR" "$HOME_DIR" > "$APP/Contents/MacOS/Aethelark"
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
Categories=Utility;Development;
DESKTOP
  command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi

step 100 "Ready."
sleep 1.2
cleanup; ANIM_PID=""

cat <<BANNER

   ${BONE}Aethelark is installed.${RESET}

   ${SLATE}Launching now. Next time, just type${RESET} ${AMBER}eagle${RESET} ${SLATE}in any terminal.${RESET}
   ${DIM}You'll need a free Gemini API key — the app walks you through it.${RESET}

BANNER

exec "$BIN_DIR/eagle"
