param(
    [string]$InstallRoot,
    [switch]$Start
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-Safely([string]$Message) {
    Write-Host "[gcode-safe] ERROR: $Message" -ForegroundColor Red
    exit 1
}

if ($env:OS -ne 'Windows_NT') {
    Stop-Safely 'This setup is intended for Windows only.'
}

if (-not $InstallRoot) {
    $InstallRoot = Split-Path -Parent $PSScriptRoot
}
$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)

$runnerPath = Join-Path $InstallRoot 'scripts\run-free-only-windows.ps1'
if (-not (Test-Path -LiteralPath $runnerPath -PathType Leaf)) {
    Stop-Safely "FREE-ONLY runner was not found: $runnerPath"
}

$debugExe = Join-Path $InstallRoot 'target\debug\gcode.exe'
$releaseExe = Join-Path $InstallRoot 'target\release\gcode.exe'
$exe = if (Test-Path -LiteralPath $debugExe -PathType Leaf) {
    $debugExe
} elseif (Test-Path -LiteralPath $releaseExe -PathType Leaf) {
    $releaseExe
} else {
    Stop-Safely 'No built gcode.exe was found. Run the low-RAM installer once to build it.'
}

# Persist telemetry opt-out now. The runner repeats this on every launch as a fail-safe.
$gcodeHome = if ($env:GCODE_HOME) {
    $env:GCODE_HOME
} elseif ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE '.gcode'
} else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.gcode'
}
New-Item -ItemType Directory -Path $gcodeHome -Force | Out-Null
New-Item -ItemType File -Path (Join-Path $gcodeHome 'no_telemetry') -Force | Out-Null

$launcherPath = Join-Path $InstallRoot 'RUN-GCODE.cmd'
$launcher = @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "$runnerPath" -GcodeExe "$exe" %*
"@
Set-Content -Path $launcherPath -Value $launcher -Encoding ASCII

Write-Host '[gcode-safe] Permanent FREE-ONLY launcher installed.' -ForegroundColor Green
Write-Host '[gcode-safe] Model: OpenRouter openrouter/free' -ForegroundColor Green
Write-Host '[gcode-safe] Telemetry: OFF' -ForegroundColor Green
Write-Host '[gcode-safe] Stale Gcode server: cleared automatically on every launch' -ForegroundColor Green
Write-Host "[gcode-safe] Launcher: $launcherPath" -ForegroundColor Green

if ($Start) {
    & $launcherPath
    exit $LASTEXITCODE
}
