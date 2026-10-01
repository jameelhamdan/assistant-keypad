# Builds dist\Keypad (keypad.exe, keypadw.exe, keypad-hook.exe) and, with
# Inno Setup installed, dist\Keypad-<version>-setup.exe.
#   powershell -ExecutionPolicy Bypass -File packaging\windows\build.ps1 -Version 2.1.0
param([string]$Version = "dev")
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..\..")
$py = "host\.venv\Scripts\python.exe"
$work = Join-Path $env:TEMP ("keypad-build-" + [guid]::NewGuid())
New-Item -ItemType Directory $work | Out-Null
try {
  & $py packaging\mkicon.py "$work\keypad.ico"
  Set-Content -NoNewline -Encoding ascii host\keypad\data\version $Version.TrimStart("v")
  $env:KEYPAD_VERSION = $Version; $env:KEYPAD_ICON = "$work\keypad.ico"
  & $py -m PyInstaller --noconfirm --clean --distpath dist --workpath "$work\build" packaging\keypad.spec
  if ($LASTEXITCODE) { throw "PyInstaller failed" }
  if (Get-Command iscc -ErrorAction SilentlyContinue) {
    iscc "/DVersion=$($Version.TrimStart('v'))" packaging\windows\keypad.iss
  } else { Write-Host "Inno Setup (iscc) not found: built dist\Keypad only" }
} finally {
  Remove-Item -Force -ErrorAction SilentlyContinue host\keypad\data\version
  Remove-Item -Recurse -Force $work
}
