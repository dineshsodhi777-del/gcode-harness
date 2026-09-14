param(
    [string]$DineshRoot = 'E:\Dinesh-One-Studio',
    [string]$GcodeRoot = 'E:\GcodeHarness',
    [int]$BridgePort = 8855,
    [Int64]$SeedProviderResetMs = 0
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Info([string]$Message) { Write-Host "[dinesh-os] $Message" -ForegroundColor Cyan }
function Fail([string]$Message) { Write-Host "[dinesh-os] ERROR: $Message" -ForegroundColor Red; exit 1 }

$DineshRoot = [System.IO.Path]::GetFullPath($DineshRoot)
$GcodeRoot = [System.IO.Path]::GetFullPath($GcodeRoot)
if (-not (Test-Path -LiteralPath $DineshRoot -PathType Container)) { Fail "Dinesh OS root not found: $DineshRoot" }
if (-not (Test-Path -LiteralPath $GcodeRoot -PathType Container)) { Fail "Gcode root not found: $GcodeRoot" }

$gcodeExe = Join-Path $GcodeRoot 'target\debug\gcode.exe'
if (-not (Test-Path -LiteralPath $gcodeExe -PathType Leaf)) { $gcodeExe = Join-Path $GcodeRoot 'target\release\gcode.exe' }
if (-not (Test-Path -LiteralPath $gcodeExe -PathType Leaf)) { Fail 'Built gcode.exe not found.' }

$bridgePy = Join-Path $GcodeRoot 'scripts\dinesh-os-gcode-bridge-v2.py'
if (-not (Test-Path -LiteralPath $bridgePy -PathType Leaf)) { Fail "Quota-gated bridge missing: $bridgePy" }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail 'Python is required and was not found in PATH.' }

$integrationDir = Join-Path $DineshRoot 'integrations\gcode'
New-Item -ItemType Directory -Force -Path $integrationDir | Out-Null
$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupRoot = Join-Path $DineshRoot "backups\pre_gcode_integration_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

$runnerPath = Join-Path $integrationDir 'run-gcode-bridge.ps1'
$restartPath = Join-Path $integrationDir 'restart-gcode-bridge.ps1'
$statePath = Join-Path $integrationDir 'provider-state.json'
$readmePath = Join-Path $integrationDir 'GCODE-INTEGRATION-README.txt'
$startPath = Join-Path $DineshRoot 'START-GCODE-WORKER.cmd'

foreach ($existing in @($runnerPath,$restartPath,$readmePath,$startPath)) {
    if (Test-Path -LiteralPath $existing -PathType Leaf) {
        Copy-Item -LiteralPath $existing -Destination (Join-Path $backupRoot ([System.IO.Path]::GetFileName($existing))) -Force
    }
}

if ($SeedProviderResetMs -gt 0) {
    $nowMs = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    if ($SeedProviderResetMs -gt $nowMs) {
        $state = [ordered]@{
            status = 'waiting_for_free_provider'
            blocked_until_ms = $SeedProviderResetMs
            reason = 'OpenRouter free daily quota exhausted'
            updated_at_ms = $nowMs
        }
        $state | ConvertTo-Json | Set-Content -LiteralPath $statePath -Encoding UTF8
        Info "Seeded WAITING_FOR_FREE_PROVIDER until reset timestamp $SeedProviderResetMs"
    }
}

$runner = @"
param()
`$ErrorActionPreference = 'Stop'
`$env:GCODE_FREE_ONLY = '1'
`$env:GCODE_ACTIVE_PROVIDER = 'openrouter'
`$env:GCODE_FORCE_PROVIDER = '1'
`$env:GCODE_OPENROUTER_MODEL = 'openrouter/free'
`$env:GCODE_NO_TELEMETRY = '1'
`$env:DO_NOT_TRACK = '1'
& '$($python.Source)' '$bridgePy' --gcode-exe '$gcodeExe' --allowed-root '$DineshRoot' --provider-state-file '$statePath' --host 127.0.0.1 --port $BridgePort
"@
Set-Content -LiteralPath $runnerPath -Value $runner -Encoding UTF8

$restart = @"
param()
`$ErrorActionPreference = 'Stop'
`$bridgeScript = '$bridgePy'
`$processes = @(Get-CimInstance Win32_Process)
function Stop-Tree([uint32]`$Pid) {
    foreach (`$child in @(`$processes | Where-Object { `$_.ParentProcessId -eq `$Pid })) { Stop-Tree -Pid ([uint32]`$child.ProcessId) }
    Stop-Process -Id `$Pid -Force -ErrorAction SilentlyContinue
}
`$targets = @(`$processes | Where-Object {
    `$_.Name -match '^python(w)?\.exe$' -and `$_.CommandLine -and `$_.CommandLine.IndexOf(`$bridgeScript,[System.StringComparison]::OrdinalIgnoreCase) -ge 0
})
foreach (`$p in `$targets) { Stop-Tree -Pid ([uint32]`$p.ProcessId) }
if (`$targets.Count -gt 0) { Start-Sleep -Milliseconds 800 }
Start-Process -FilePath 'powershell.exe' -ArgumentList @('-NoProfile','-ExecutionPolicy','Bypass','-File','"$runnerPath"')
"@
Set-Content -LiteralPath $restartPath -Value $restart -Encoding UTF8

$readme = @"
Dinesh OS <-> Gcode Integration

Panel: http://127.0.0.1:$BridgePort/
Health: http://127.0.0.1:$BridgePort/health

Safety and provider policy:
- localhost only
- FREE_ONLY mandatory
- OpenRouter model locked to openrouter/free
- paid-provider fallback blocked by Gcode core
- telemetry disabled
- working directory restricted to $DineshRoot
- 429 free-daily-quota responses are persisted as WAITING_FOR_FREE_PROVIDER
- while waiting, new tasks fail fast without launching Gcode
- wait state clears automatically after provider reset time

Backup: $backupRoot
"@
Set-Content -LiteralPath $readmePath -Value $readme -Encoding UTF8

$startCmd = @"
@echo off
powershell -NoProfile -ExecutionPolicy Bypass -File "$restartPath"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:$BridgePort/"
"@
Set-Content -LiteralPath $startPath -Value $startCmd -Encoding ASCII

Info "Integration installed: $integrationDir"
Info "Backup: $backupRoot"
Info "Panel URL: http://127.0.0.1:$BridgePort/"
Info 'PASS: fail-fast FREE_ONLY provider gate installed.'
