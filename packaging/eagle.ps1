# Aethelark launcher for Windows. THIS FILE IS THE SOURCE; install.ps1 renders it
# to %LOCALAPPDATA%\Aethelark\bin\eagle.ps1 with the app directory filled in, and
# `eagle.cmd` calls it. It does what packaging/eagle does on macOS and Linux:
#
#   eagle                    open the eagle
#   eagle install <module>   eagle remove <module>   eagle modules   eagle update
#   eagle --doctor           anything else goes to the app, in this terminal
#
# An eagle that is already running keeps the old code in memory, so it is
# stopped before a new one starts.
$ErrorActionPreference = "Stop"
# The app and its installer print characters (a tick, an emoji) that a legacy
# Windows console code page cannot encode; Python would crash on the print.
$env:PYTHONUTF8 = "1"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}
$Repo = "@AETHELARK_APP@"
$Py   = Join-Path $Repo ".venv\Scripts\python.exe"
$PyW  = Join-Path $Repo ".venv\Scripts\pythonw.exe"
Set-Location $Repo

function Stop-OlderEagle {
  $old = Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match "aethelark_web\.py" -and $_.ProcessId -ne $PID }
  foreach ($p in $old) {
    Write-Host "eagle: an older eagle is running (pid $($p.ProcessId)) - stopping it, it has the old code in memory"
    Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
  }
  if ($old) { Start-Sleep -Milliseconds 1500 }
}

$first = if ($args.Count -gt 0) { [string]$args[0] } else { "" }
$rest  = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

switch ($first) {
  { $_ -in "install", "remove", "uninstall", "modules", "bundle" } {
    $sub = if ($first -eq "uninstall") { "remove" } else { $first }
    & $Py -m core.module_bus.installer $sub @rest
    exit $LASTEXITCODE
  }
  "update" {
    # Only a checkout the installer made is reset; a developer's working copy
    # has commits that exist nowhere else.
    if (Test-Path (Join-Path $Repo ".aethelark-install")) {
      $branch = (git -C $Repo rev-parse --abbrev-ref HEAD 2>$null)
      if (-not $branch) { $branch = "main" }
      Write-Host "eagle: updating the app ($branch)..."
      git -C $Repo fetch --quiet --depth 1 origin $branch
      if ($LASTEXITCODE -eq 0) {
        git -C $Repo reset --hard --quiet FETCH_HEAD
        $uv = Get-Command uv -ErrorAction SilentlyContinue
        $cons = @()
        if (Test-Path (Join-Path $Repo "constraints.txt")) { $cons = @("-c", (Join-Path $Repo "constraints.txt")) }
        if ($uv) {
          & uv pip install --quiet --python $Py -r (Join-Path $Repo "requirements.txt") @cons
        } else {
          & $Py -m pip install --quiet -r (Join-Path $Repo "requirements.txt") @cons
        }
        Write-Host ("eagle: the app is at " + (git -C $Repo log -1 --format="%h %s"))
        # The launcher is a copy too: refresh it from the repo's.
        $src = Join-Path $Repo "packaging\eagle.ps1"
        if (Test-Path $src) {
          # The placeholder is built from two halves so the installer's own
          # substitution, which runs over this whole file, cannot rewrite it.
          $ph = "@AETHELARK" + "_APP@"
          $text = (Get-Content -Raw $src).Replace($ph, $Repo)
          Set-Content -Encoding UTF8 -Path (Join-Path (Split-Path $PSCommandPath) "eagle.ps1.new") -Value $text
          Move-Item -Force (Join-Path (Split-Path $PSCommandPath) "eagle.ps1.new") (Join-Path (Split-Path $PSCommandPath) "eagle.ps1")
        }
      } else {
        Write-Host "eagle: could not reach the app's repository, so the app was not updated." -ForegroundColor Yellow
      }
    } else {
      Write-Host "eagle: $Repo is a development checkout, not one the installer made - update it with git. Updating modules only."
    }
    & $Py -m core.module_bus.installer update @rest
    exit $LASTEXITCODE
  }
}

if ($args.Count -gt 0) {
  # A flag such as --doctor: run in this terminal, with its output.
  & $Py aethelark_web.py @args
  exit $LASTEXITCODE
}

Stop-OlderEagle

# Someone who installed the eagle gets no log file. A checkout the installer did
# not make (a developer's) keeps one; EAGLE_LOG=1 turns it on for an installed one.
$installed = Test-Path (Join-Path $Repo ".aethelark-install")
if ($installed -and -not $env:EAGLE_LOG) {
  $exe = if (Test-Path $PyW) { $PyW } else { $Py }
  Start-Process -FilePath $exe -ArgumentList "aethelark_web.py" -WorkingDirectory $Repo -WindowStyle Hidden
  Write-Host "eagle: opening"
  exit 0
}

$logDir = Join-Path $env:LOCALAPPDATA "Aethelark\logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "eagle.log"
if ((Test-Path $log) -and ((Get-Item $log).Length -gt 5000000)) { Move-Item -Force $log "$log.1" }
Add-Content -Path $log -Value ("=== " + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + " ===")
& $Py aethelark_web.py 2>&1 | Tee-Object -FilePath $log -Append
