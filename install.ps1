# Aethelark installer - Windows.
#
#   irm https://get.aethelark.com/install.ps1 | iex
#
# Per-user install (no admin, no UAC). Same layout as macOS and Linux: the app is
# a git checkout in %LOCALAPPDATA%\Aethelark\app, a private Python comes from uv,
# an `eagle` command goes on PATH, and modules live under ~\.aethelark.
#
# Knobs, all optional:
#   AETHELARK_APP=<dir>       where the app goes
#   AETHELARK_REPO=<url|dir>  where to get it from
#   AETHELARK_BRANCH=<name>   which branch (default main)
#   AETHELARK_MODULES=<list>  modules to add, comma separated (default trade; none skips)
#   AETHELARK_NO_LAUNCH=1     install, but do not start the eagle
#   AETHELARK_SKIP_BROWSER=1  skip the eagle's own browser (~150 MB)

# Native tools (git, uv) write progress to stderr; under "Stop" Windows PowerShell
# 5.1 turns that into an error. Exit codes are checked explicitly instead.
$ErrorActionPreference = "Continue"
$ProgressPreference    = "SilentlyContinue"
$env:PYTHONUTF8        = "1"   # see packaging/eagle.ps1

$Repo      = if ($env:AETHELARK_REPO)   { $env:AETHELARK_REPO }   else { "https://github.com/ciopialex/Project-Space-Eagle.git" }
$Branch    = if ($env:AETHELARK_BRANCH) { $env:AETHELARK_BRANCH } else { "main" }
$EagleHome = Join-Path $env:LOCALAPPDATA "Aethelark"
$AppDir    = if ($env:AETHELARK_APP) { $env:AETHELARK_APP } elseif ($env:AETHELARK_HOME) { $env:AETHELARK_HOME } else { Join-Path $EagleHome "app" }
$BinDir    = Join-Path $EagleHome "bin"
$PyVer     = "3.12"
$Modules   = if ($env:AETHELARK_MODULES) { $env:AETHELARK_MODULES } else { "trade" }

# MinGit, pinned by hash: the portable git used when the machine has none.
$MinGitVer = "2.47.1"
$MinGit = @{
  "AMD64" = @{ File = "MinGit-2.47.1-64-bit.zip"; Sha256 = "50b04b55425b5c465d076cdb184f63a0cd0f86f6ec8bb4d5860114a713d2c29a" }
  "ARM64" = @{ File = "MinGit-2.47.1-arm64.zip";  Sha256 = "fc5747e187a70147404a94da104dc9f6005a3d45a78a56dbfa132075ad4a45e4" }
}

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
  try { Clear-Host } catch {}
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

function Ensure-Git {
  if (Get-Command git -ErrorAction SilentlyContinue) { return }
  $arch = if ($env:PROCESSOR_ARCHITECTURE -eq "ARM64") { "ARM64" } else { "AMD64" }
  $pick = $MinGit[$arch]
  $zip  = Join-Path $env:TEMP $pick.File
  $url  = "https://github.com/git-for-windows/git/releases/download/v$MinGitVer.windows.1/$($pick.File)"
  try { Invoke-WebRequest -Uri $url -OutFile $zip -UseBasicParsing } catch { Die "could not download git (needed to fetch Aethelark): $_" }
  $got = (Get-FileHash -Path $zip -Algorithm SHA256).Hash.ToLower()
  if ($got -ne $pick.Sha256) { Remove-Item -Force $zip; Die "the git download did not match its checksum, so it was not used" }
  $dest = Join-Path $EagleHome "git"
  Expand-Archive -Path $zip -DestinationPath $dest -Force
  Remove-Item -Force $zip
  $env:Path = "$dest\cmd;$env:Path"
  $userPath = [Environment]::GetEnvironmentVariable("Path", "User")
  if ($userPath -notlike "*$dest\cmd*") { [Environment]::SetEnvironmentVariable("Path", "$dest\cmd;$userPath", "User") }
  if (-not (Get-Command git -ErrorAction SilentlyContinue)) { Die "git was unpacked but is not on PATH" }
}

Show-Crest 6 "Fetching the runtime..."
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  try { irm https://astral.sh/uv/install.ps1 | iex } catch { Die "could not install uv (needed to provide Python)" }
}
$env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Die "uv installed but is not on PATH" }

Show-Crest 22 "Installing Python $PyVer..."
uv python install $PyVer 2>&1 | Out-Null
if ($LASTEXITCODE -ne 0) { Die "could not install Python $PyVer" }

Show-Crest 34 "Downloading Aethelark..."
Ensure-Git
New-Item -ItemType Directory -Force -Path (Split-Path $AppDir) | Out-Null
if (Test-Path (Join-Path $AppDir ".git")) {
  # Only ever update a checkout that is actually ours - reset --hard inside
  # someone else's repository would destroy their uncommitted work.
  $ExistingRemote = (git -C $AppDir remote get-url origin 2>$null)
  if ($ExistingRemote -notmatch "Space-Eagle") {
    Die "$AppDir is a git repository, but not Aethelark's ($ExistingRemote). Refusing to touch it. Install elsewhere by setting AETHELARK_APP first."
  }
  git -C $AppDir fetch --quiet --depth 1 origin $Branch 2>&1 | Out-Null
  git -C $AppDir reset --hard --quiet FETCH_HEAD 2>&1 | Out-Null
} elseif ((Test-Path $AppDir) -and (Get-ChildItem -Force $AppDir | Select-Object -First 1)) {
  # Never destroy a directory we did not create.
  Die "$AppDir already exists and is not empty, and is not an Aethelark checkout. Refusing to delete it. Move it aside, or set AETHELARK_APP to another path."
} else {
  git clone --quiet --depth 1 --branch $Branch $Repo $AppDir 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0 -or -not (Test-Path (Join-Path $AppDir ".git"))) { Die "could not download Aethelark from $Repo" }
}
# Marks a checkout this installer made, which `eagle update` may reset.
New-Item -ItemType File -Force -Path (Join-Path $AppDir ".aethelark-install") | Out-Null

Show-Crest 46 "Building the environment..."
$VenvPy = Join-Path $AppDir ".venv\Scripts\python.exe"
if (-not (Test-Path $VenvPy)) {
  uv venv --python $PyVer (Join-Path $AppDir ".venv") 2>&1 | Out-Null
  if ($LASTEXITCODE -ne 0) { Die "could not create the virtual environment" }
}

Show-Crest 58 "Installing dependencies (this is the long one)..."
$cons = @()
if (Test-Path (Join-Path $AppDir "constraints.txt")) { $cons = @("-c", (Join-Path $AppDir "constraints.txt")) }
$depOut = uv pip install --python $VenvPy -q -r (Join-Path $AppDir "requirements.txt") @cons 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Die "dependency install failed:`n$($depOut.Trim() -split "`n" | Select-Object -Last 8 | Out-String)" }

Show-Crest 78 "Verifying..."
$importErr = & $VenvPy -c "import PyQt6.QtWebEngineWidgets, google.genai, sounddevice" 2>&1 | Out-String
if ($LASTEXITCODE -ne 0) { Die "the install is missing critical components:`n$($importErr.Trim() -split "`n" | Select-Object -Last 6 | Out-String)" }

if ($env:AETHELARK_SKIP_BROWSER -ne "1") {
  Show-Crest 82 "Fetching the eagle's own browser..."
  # Not fatal: everything else works without it, and `eagle --doctor` says so.
  & $VenvPy -m playwright install chromium 2>&1 | Out-Null
}

Show-Crest 88 "Linking the ``eagle`` command..."
New-Item -ItemType Directory -Force -Path $BinDir | Out-Null
# The launcher is a tracked file, rendered with this machine's app directory.
# Split token: this line is itself inside the file being written.
$ph = "@AETHELARK" + "_APP@"
$launcher = (Get-Content -Raw (Join-Path $AppDir "packaging\eagle.ps1")).Replace($ph, $AppDir)
Set-Content -Encoding UTF8 -Path (Join-Path $BinDir "eagle.ps1") -Value $launcher
"@echo off`r`npowershell -NoProfile -ExecutionPolicy Bypass -File ""%~dp0eagle.ps1"" %*" |
  Set-Content -Encoding ASCII (Join-Path $BinDir "eagle.cmd")
Copy-Item -Force (Join-Path $AppDir "packaging\uninstall.ps1") (Join-Path $BinDir "uninstall.ps1")

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath -notlike "*$BinDir*") {
  [Environment]::SetEnvironmentVariable("Path", "$BinDir;$userPath", "User")
}
$env:Path = "$BinDir;$env:Path"

Show-Crest 92 "Creating the app icon..."
$ico = Join-Path $AppDir "config\aethelark.ico"
function New-EagleShortcut($Path) {
  try {
    $sc = (New-Object -ComObject WScript.Shell).CreateShortcut($Path)
    $sc.TargetPath = (Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe")
    $sc.Arguments = "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$(Join-Path $BinDir 'eagle.ps1')`""
    $sc.WorkingDirectory = $AppDir
    if (Test-Path $ico) { $sc.IconLocation = $ico }
    $sc.Description = "Voice-commanded operator for your machine"
    $sc.Save()
  } catch {}
}
# A machine can have no Desktop or Start Menu folder (a locked-down profile, a
# service account); a missing one is skipped, never an error.
foreach ($where in "Programs", "Desktop") {
  $dir = [Environment]::GetFolderPath($where)
  if ($dir -and (Test-Path $dir)) { New-EagleShortcut (Join-Path $dir "Aethelark.lnk") }
}

# Settings > Apps > Aethelark > Uninstall.
try {
  $key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Aethelark"
  New-Item -Force -Path $key | Out-Null
  Set-ItemProperty -Path $key -Name DisplayName -Value "Aethelark"
  Set-ItemProperty -Path $key -Name Publisher -Value "Aethelark"
  Set-ItemProperty -Path $key -Name InstallLocation -Value $AppDir
  if (Test-Path $ico) { Set-ItemProperty -Path $key -Name DisplayIcon -Value $ico }
  Set-ItemProperty -Path $key -Name UninstallString -Value "powershell -NoProfile -ExecutionPolicy Bypass -File `"$(Join-Path $BinDir 'uninstall.ps1')`""
} catch {}

if ($Modules -ne "none") {
  Show-Crest 95 "Adding the modules..."
  # `bundle` leaves alone any module the user removed.
  $names = @($Modules -split "," | ForEach-Object { $_.Trim() } | Where-Object { $_ })
  & $VenvPy -m core.module_bus.installer bundle @names 2>&1 | Out-Null
}

Show-Crest 100 "Ready."
Start-Sleep -Milliseconds 1200

Write-Host "`n   Aethelark is installed." -ForegroundColor White
Write-Host "   Next time, just type " -NoNewline -ForegroundColor Gray
Write-Host "eagle" -NoNewline -ForegroundColor Yellow
Write-Host " in any terminal, or open it from the Start Menu or your Desktop." -ForegroundColor Gray
Write-Host "   You'll need a free Gemini API key - the app walks you through it.`n" -ForegroundColor DarkGray

if ($env:AETHELARK_NO_LAUNCH -eq "1") { exit 0 }
& (Join-Path $BinDir "eagle.cmd")
