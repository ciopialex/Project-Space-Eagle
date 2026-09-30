# Removes the eagle from this Windows account: the app, the `eagle` command, the
# shortcuts and the Settings > Apps entry. The user's own data (keys, memory,
# modules) is left behind unless -Everything is given, so uninstalling to fix a
# problem does not cost them their setup.
param([switch]$Everything)
$ErrorActionPreference = "Continue"

$EagleHome = Join-Path $env:LOCALAPPDATA "Aethelark"
$BinDir    = Join-Path $EagleHome "bin"
$AppDir    = Join-Path $EagleHome "app"

Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
  Where-Object { $_.CommandLine -and $_.CommandLine -match "aethelark_web\.py" } |
  ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }

foreach ($where in "Programs", "Desktop") {
  $dir = [Environment]::GetFolderPath($where)
  if ($dir) { Remove-Item -Force -ErrorAction SilentlyContinue (Join-Path $dir "Aethelark.lnk") }
}

$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($userPath) {
  $kept = ($userPath -split ";" | Where-Object { $_ -and $_ -ne $BinDir -and $_ -ne (Join-Path $EagleHome "git\cmd") }) -join ";"
  [Environment]::SetEnvironmentVariable("Path", $kept, "User")
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Aethelark"
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $AppDir
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $EagleHome "git")
if ($Everything) {
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $EagleHome
  Remove-Item -Recurse -Force -ErrorAction SilentlyContinue (Join-Path $env:USERPROFILE ".aethelark")
}
Remove-Item -Recurse -Force -ErrorAction SilentlyContinue $BinDir
Write-Host "Aethelark was removed. Your keys and memory were kept; run with -Everything to remove those too."
