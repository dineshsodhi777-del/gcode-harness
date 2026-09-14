param(
    [Parameter(Mandatory = $true)]
    [string]$GcodeExe,

    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$GcodeArgs = @()
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Stop-Safely([string]$Message) {
    Write-Host "[gcode-safe] ERROR: $Message" -ForegroundColor Red
    exit 1
}

if ($env:OS -ne 'Windows_NT') {
    Stop-Safely 'This runner is intended for Windows only.'
}

$targetExe = [System.IO.Path]::GetFullPath($GcodeExe)
if (-not (Test-Path -LiteralPath $targetExe -PathType Leaf)) {
    Stop-Safely "gcode.exe was not found: $targetExe"
}

# CMD/PowerShell wrappers may supply a standalone -- separator. It is not forwarded.
$forwardArgs = @($GcodeArgs | Where-Object { $_ -and $_ -ne '--' })

# FREE-ONLY means callers cannot override the provider/model through this launcher.
$blockedArgs = @('--provider', '-p', '--model', '-m', '--provider-profile')
foreach ($arg in $forwardArgs) {
    $argName = ($arg -split '=', 2)[0].ToLowerInvariant()
    if ($blockedArgs -contains $argName) {
        Stop-Safely 'Provider/model overrides are disabled by the FREE-ONLY launcher. Use RUN-GCODE.cmd without provider/model overrides.'
    }
}

# Disable telemetry in both the child environment and the persistent gcode opt-out file.
$env:GCODE_NO_TELEMETRY = '1'
$env:DO_NOT_TRACK = '1'

$gcodeHome = if ($env:GCODE_HOME) {
    $env:GCODE_HOME
} elseif ($env:USERPROFILE) {
    Join-Path $env:USERPROFILE '.gcode'
} else {
    Join-Path ([Environment]::GetFolderPath('UserProfile')) '.gcode'
}

try {
    New-Item -ItemType Directory -Path $gcodeHome -Force | Out-Null
    New-Item -ItemType File -Path (Join-Path $gcodeHome 'no_telemetry') -Force | Out-Null
} catch {
    Stop-Safely "Could not enforce the persistent telemetry opt-out: $($_.Exception.Message)"
}

# Gcode uses a shared background server. A server left from an older session can keep
# its old model and ignore new --provider/--model startup flags. On this low-RAM,
# single-session launcher we stop only processes using this exact gcode.exe, then
# start one fresh FREE-ONLY server. Other Gcode installations are left untouched.
try {
    $targetNormalized = $targetExe.ToLowerInvariant()
    $existing = @(
        Get-CimInstance Win32_Process -Filter "Name = 'gcode.exe'" -ErrorAction Stop |
            Where-Object {
                $_.ExecutablePath -and
                ([System.IO.Path]::GetFullPath($_.ExecutablePath).ToLowerInvariant() -eq $targetNormalized)
            }
    )
    foreach ($process in $existing) {
        try {
            Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
        } catch {
            # A process may have exited between enumeration and Stop-Process. Only fail
            # closed if the process is still present.
            if (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue) {
                throw
            }
        }
    }
    if ($existing.Count -gt 0) {
        Start-Sleep -Milliseconds 750
    }
} catch {
    Stop-Safely "Could not safely clear an existing Gcode server: $($_.Exception.Message)"
}

Write-Host '[gcode-safe] FREE-ONLY: OpenRouter openrouter/free' -ForegroundColor Green
Write-Host '[gcode-safe] Telemetry: OFF' -ForegroundColor Green
Write-Host '[gcode-safe] Stale server for this Gcode binary: cleared before launch' -ForegroundColor Green

$fixedArgs = @(
    '--no-update',
    '--provider', 'openrouter',
    '--model', 'openrouter/free'
)

& $targetExe @fixedArgs @forwardArgs
$exitCode = $LASTEXITCODE
if ($null -eq $exitCode) { $exitCode = 0 }
exit $exitCode
