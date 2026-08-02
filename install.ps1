# Aethelark installer - Windows.
#
#   irm https://get.aethelark.com/install.ps1 | iex
#
# Per-user install (no admin, no UAC). Windows support is BETA: the app's
# win32 code paths have never been executed on a real Windows machine, so
# expect rough edges and please report them.
$ErrorActionPreference = "Stop"

$Repo    = if ($env:AETHELARK_REPO) { $env:AETHELARK_REPO } else { "https://github.com/ciopialex/Project-Space-Eagle.git" }
$HomeDir = if ($env:AETHELARK_HOME) { $env:AETHELARK_HOME } else { Join-Path $env:LOCALAPPDATA "Aethelark" }
$BinDir  = Join-Path $env:LOCALAPPDATA "Aethelark\bin"
$PyVer   = "3.12"

$Eagle = @(
  '`w_                                                  _w''',
  '  *@g_                                            _g@K',
  '    M@@g_                                      _g@@M',
  '      M@@@g_             @@@MWmg_            ,@@@M`',
  '       ^W@@@@g_         @@@@@@@@@@y       _@@@@W^'
)
$EagleL = @('       ^w^W@@@@@g_  ', '         Mw^M@@@@@@,', '          ^W@g*W@@@@', '            MW@@,*W@')
$EagleR = @('   _@@@@@MK,^', '_@@@@@@W*gP', '@@@@WM_@@C', '@WM_@@@M`')
$EagleB = @(
  '              "W@@y^W@@@@@@@@@K@@@@@K,@@MM',
  '                ^W@W M@@@@@@@@@@@@W`@@WM',
  '                  ^M@_^@@@@@@@@@@M_@W^',
  '                     W@ M@@@@@@W^,W^',
  '                      M@_^@@@@M @M',
  '                       ^@y WW^_@M',
  '                         WW  g@C',
  '                          M@@W`',
  '                           MM'
)
$CoreW = 14

function Show-Crest($Pct, $Label) {
  Clear-Host
  Write-Host ""
  foreach ($l in $Eagle) { Write-Host "  $l" -ForegroundColor DarkGray }

  $filled = [math]::Round($CoreW * $Pct / 100)
  $bar = ("$([char]0x2588)" * $filled) + ("$([char]0x2591)" * ($CoreW - $filled))
  $lab = "{0,4}" -f "$Pct%"
  $pad = [math]::Floor(($CoreW - 4) / 2)
  $lab = (" " * $pad) + $lab + (" " * ($CoreW - 4 - $pad))

  Write-Host ("  " + $EagleL[0]) -NoNewline -ForegroundColor DarkGray
  Write-Host ("$([char]0x2597)" + ("$([char]0x2584)" * $CoreW) + "$([char]0x2596)") -NoNewline -ForegroundColor DarkYellow
  Write-Host $EagleR[0] -ForegroundColor DarkGray

  Write-Host ("  " + $EagleL[1]) -NoNewline -ForegroundColor DarkGray
  Write-Host "$([char]0x2590)" -NoNewline -ForegroundColor DarkYellow
  Write-Host $bar -NoNewline -ForegroundColor Yellow
  Write-Host "$([char]0x258C)" -NoNewline -ForegroundColor DarkYellow
  Write-Host $EagleR[1] -ForegroundColor DarkGray

  Write-Host ("  " + $EagleL[2]) -NoNewline -ForegroundColor DarkGray
  Write-Host "$([char]0x2590)" -NoNewline -ForegroundColor DarkYellow
  Write-Host $lab -NoNewline -ForegroundColor White
  Write-Host "$([char]0x258C)" -NoNewline -ForegroundColor DarkYellow
  Write-Host $EagleR[2] -ForegroundColor DarkGray

  Write-Host ("  " + $EagleL[3]) -NoNewline -ForegroundColor DarkGray
  Write-Host ("$([char]0x259D)" + ("$([char]0x2580)" * $CoreW) + "$([char]0x2598)") -NoNewline -ForegroundColor DarkYellow
  Write-Host $EagleR[3] -ForegroundColor DarkGray

  foreach ($l in $EagleB) { Write-Host "  $l" -ForegroundColor DarkGray }
  Write-Host ""
  Write-Host "   $Label" -ForegroundColor Gray
}

function Die($msg) { Write-Host "`n Install failed: $msg`n" -ForegroundColor Red; exit 1 }

try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

Show-Crest 6 "Fetching the runtime..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  try { irm https://astral.sh/uv/install.ps1 | iex } catch { Die "could not install uv (needed to provide Python)" }
}
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Die "uv installed but is not on PATH" }

Show-Crest 22 "Installing Python $PyVer..."
uv python install $PyVer 2>&1 | Out-Null

Show-Crest 34 "Downloading Aethelark..."
if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git is required. Install it from https://git-scm.com and re-run." }
if (Test-Path (Join-Path $HomeDir ".git")) {
  # Only ever update a checkout that is actually ours - reset --hard inside
  # someone else's repository would destroy their uncommitted work.
  $ExistingRemote = (git -C $HomeDir remote get-url origin 2>$null)
  if ($ExistingRemote -notmatch "Space-Eagle") {
    Die "$HomeDir is a git repository, but not Aethelark's ($ExistingRemote). Refusing to touch it. Install elsewhere by setting AETHELARK_HOME first."
  }
  git -C $HomeDir fetch --quiet --depth 1 origin main 2>&1 | Out-Null
  git -C $HomeDir reset --hard --quiet origin/main 2>&1 | Out-Null
} elseif ((Test-Path $HomeDir) -and (Get-ChildItem -Force $HomeDir | Select-Object -First 1)) {
  # This used to be Remove-Item -Recurse -Force. AETHELARK_HOME is user-settable
  # and the default name is one other tools use too, so that could silently
  # delete unrelated data. Never destroy a directory we did not create.
  Die "$HomeDir already exists and is not empty, and is not an Aethelark checkout. Refusing to delete it. Move it aside, or set AETHELARK_HOME to another path."
} else {
  git clone --quiet --depth 1 $Repo $HomeDir 2>&1 | Out-Null
  if (-not (Test-Path $HomeDir)) { Die "could not download Aethelark" }
}

Show-Crest 46 "Building the environment..."
uv venv --python $PyVer (Join-Path $HomeDir ".venv") 2>&1 | Out-Null
$VenvPy = Join-Path $HomeDir ".venv\Scripts\python.exe"

Show-Crest 58 "Installing dependencies (this is the long one)..."
uv pip install --python $VenvPy -q -r (Join-Path $HomeDir "requirements.txt") 2>&1 | Out-Null

Show-Crest 82 "Verifying..."
& $VenvPy -c "import PyQt6.QtWebEngineWidgets, google.genai" 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Die "the install is missing critical components" }

Show-Crest 90 "Linking the ``eagle`` command..."
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
@"
@echo off
cd /d "$HomeDir"
"$VenvPy" aethelark_web.py %*
"@ | Set-Content -Encoding ASCII (Join-Path $BinDir "eagle.cmd")

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
  [Environment]::SetEnvironmentVariable("Path", "$BinDir;$userPath", "User")
}

# A Start Menu shortcut, so it is reachable without a terminal at all.
try {
  $sm = [Environment]::GetFolderPath("Programs")
  $sc = (New-Object -ComObject WScript.Shell).CreateShortcut((Join-Path $sm "Aethelark.lnk"))
  $sc.TargetPath = Join-Path $BinDir "eagle.cmd"
  $sc.WorkingDirectory = $HomeDir
  $ico = Join-Path $HomeDir "config\aethelark.ico"
  if (Test-Path $ico) { $sc.IconLocation = $ico }
  $sc.Save()
} catch {}

Show-Crest 100 "Ready."
Start-Sleep -Milliseconds 1200

Write-Host "`n   Aethelark is installed." -ForegroundColor White
Write-Host "   Launching now. Next time, just type " -NoNewline -ForegroundColor Gray
Write-Host "eagle" -NoNewline -ForegroundColor Yellow
Write-Host " in any terminal." -ForegroundColor Gray
Write-Host "   You'll need a free Gemini API key - the app walks you through it.`n" -ForegroundColor DarkGray

& (Join-Path $BinDir "eagle.cmd")
