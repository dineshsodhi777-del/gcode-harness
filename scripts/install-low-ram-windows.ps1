param(
    [string]$InstallRoot,
    [switch]$ReleaseBuild
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$ExpectedRepo = 'https://github.com/dineshsodhi777-del/gcode-harness.git'
$ExpectedRepoShort = 'dineshsodhi777-del/gcode-harness'
$Branch = 'master'

function Write-Info([string]$Message) {
    Write-Host "[gcode-safe] $Message" -ForegroundColor Cyan
}

function Stop-Safely([string]$Message) {
    Write-Host "[gcode-safe] ERROR: $Message" -ForegroundColor Red
    exit 1
}

function Require-Command([string]$Name, [string]$HelpText) {
    if (-not (Get-Command $Name -ErrorAction SilentlyContinue)) {
        Stop-Safely "$Name is required. $HelpText"
    }
}

if ($env:OS -ne 'Windows_NT') {
    Stop-Safely 'This installer is intended for Windows only.'
}

Require-Command 'git' 'Install Git for Windows from the official Git website, then re-run this script.'
Require-Command 'cargo' 'Install the official Rust toolchain with rustup, then re-run this script.'

if (-not $InstallRoot) {
    if (Test-Path 'E:\') {
        $InstallRoot = 'E:\GcodeHarness'
    } else {
        $InstallRoot = Join-Path $env:USERPROFILE 'GcodeHarness'
    }
}

$InstallRoot = [System.IO.Path]::GetFullPath($InstallRoot)
$Parent = Split-Path -Parent $InstallRoot
if (-not (Test-Path $Parent)) {
    New-Item -ItemType Directory -Force -Path $Parent | Out-Null
}

Write-Info "Install root: $InstallRoot"
Write-Info 'Safe mode: no package managers, no startup entries, no global hotkeys, no PATH modification.'

if (Test-Path $InstallRoot) {
    if (-not (Test-Path (Join-Path $InstallRoot '.git'))) {
        Stop-Safely "The destination already exists and is not a Git repository: $InstallRoot"
    }

    Push-Location $InstallRoot
    try {
        $origin = (git remote get-url origin 2>$null).Trim()
        if (-not $origin) {
            Stop-Safely 'Existing repository has no origin remote.'
        }

        $originNormalized = $origin.TrimEnd('/').ToLowerInvariant()
        $expectedHttps = $ExpectedRepo.TrimEnd('/').ToLowerInvariant()
        $expectedSsh = 'git@github.com:' + $ExpectedRepoShort.ToLowerInvariant() + '.git'
        if ($originNormalized -ne $expectedHttps -and $originNormalized -ne $expectedSsh) {
            Stop-Safely "Refusing to update unexpected repository origin: $origin"
        }

        $dirty = git status --porcelain
        if ($dirty) {
            Stop-Safely 'Local changes are present. Commit/stash them before updating; nothing was overwritten.'
        }

        Write-Info 'Updating repository with fast-forward only...'
        git fetch origin $Branch
        if ($LASTEXITCODE -ne 0) { Stop-Safely 'git fetch failed.' }
        git checkout $Branch
        if ($LASTEXITCODE -ne 0) { Stop-Safely 'git checkout failed.' }
        git merge --ff-only "origin/$Branch"
        if ($LASTEXITCODE -ne 0) { Stop-Safely 'Fast-forward update failed; repository was not force-reset.' }
    } finally {
        Pop-Location
    }
} else {
    Write-Info 'Cloning your verified fork...'
    git clone --branch $Branch --single-branch $ExpectedRepo $InstallRoot
    if ($LASTEXITCODE -ne 0) { Stop-Safely 'git clone failed.' }
}

Push-Location $InstallRoot
try {
    $env:CARGO_BUILD_JOBS = '1'
    $env:CARGO_INCREMENTAL = '0'

    Write-Info 'Checking Rust toolchain...'
    cargo --version
    if ($LASTEXITCODE -ne 0) { Stop-Safely 'cargo is not working correctly.' }

    $lockPath = Join-Path $InstallRoot 'Cargo.lock'
    if (-not (Test-Path $lockPath)) {
        Write-Info 'Generating a local Cargo.lock for this installation...'
        cargo generate-lockfile
        if ($LASTEXITCODE -ne 0 -or -not (Test-Path $lockPath)) {
            Stop-Safely 'Could not generate Cargo.lock. No build was started.'
        }
    } else {
        Write-Info 'Using the existing local Cargo.lock.'
    }

    Write-Info 'Running a single-job compile check for the gcode binary...'
    cargo check --locked --bin gcode -j 1
    if ($LASTEXITCODE -ne 0) { Stop-Safely 'cargo check failed. No launcher was created.' }

    $profile = if ($ReleaseBuild) { 'release' } else { 'debug' }
    $buildArgs = @('build', '--locked', '--bin', 'gcode', '-j', '1')
    if ($ReleaseBuild) {
        $buildArgs += '--release'
    }

    Write-Info "Building gcode ($profile, one job to reduce RAM pressure)..."
    & cargo @buildArgs
    if ($LASTEXITCODE -ne 0) { Stop-Safely 'cargo build failed.' }

    $exe = Join-Path $InstallRoot "target\$profile\gcode.exe"
    if (-not (Test-Path $exe)) {
        Stop-Safely "Build finished but gcode.exe was not found at: $exe"
    }

    Write-Info 'Verifying the built binary launches...'
    & $exe --version
    if ($LASTEXITCODE -ne 0) { Stop-Safely 'Built gcode.exe failed the --version smoke test.' }

    $runnerPath = Join-Path $InstallRoot 'scripts\run-free-only-windows.ps1'
    if (-not (Test-Path $runnerPath)) {
        Stop-Safely "FREE-ONLY runner was not found at: $runnerPath"
    }

    # Persistent privacy opt-out. The launcher also sets environment variables on every run.
    $gcodeHome = if ($env:GCODE_HOME) { $env:GCODE_HOME } else { Join-Path $env:USERPROFILE '.gcode' }
    New-Item -ItemType Directory -Path $gcodeHome -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $gcodeHome 'no_telemetry') -Force | Out-Null

    $launcherPath = Join-Path $InstallRoot 'RUN-GCODE.cmd'
    $launcher = @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "$runnerPath" -GcodeExe "$exe" %*
"@
    Set-Content -Path $launcherPath -Value $launcher -Encoding ASCII

    Write-Info 'SUCCESS'
    Write-Host "Launcher: $launcherPath" -ForegroundColor Green
    Write-Host 'Permanent safety policy:' -ForegroundColor Green
    Write-Host '  - OpenRouter provider locked to openrouter/free' -ForegroundColor Green
    Write-Host '  - Telemetry disabled on every launch' -ForegroundColor Green
    Write-Host '  - Existing stale gcode server cleared before launch' -ForegroundColor Green
    Write-Host '  - Provider/model overrides rejected by this launcher' -ForegroundColor Green
    Write-Host ''
    Write-Host 'Run:' -ForegroundColor Yellow
    Write-Host "  & '$launcherPath'"
} finally {
    Pop-Location
}
