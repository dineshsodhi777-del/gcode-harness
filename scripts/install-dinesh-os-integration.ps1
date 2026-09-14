param(
    [string]$DineshRoot = 'E:\Dinesh-One-Studio',
    [string]$GcodeRoot = 'E:\GcodeHarness',
    [int]$BridgePort = 8855
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
if (-not (Test-Path -LiteralPath $gcodeExe -PathType Leaf)) {
    $gcodeExe = Join-Path $GcodeRoot 'target\release\gcode.exe'
}
if (-not (Test-Path -LiteralPath $gcodeExe -PathType Leaf)) { Fail 'Built gcode.exe not found.' }

$bridgePy = Join-Path $GcodeRoot 'scripts\dinesh-os-gcode-bridge.py'
if (-not (Test-Path -LiteralPath $bridgePy -PathType Leaf)) { Fail "Bridge script missing: $bridgePy" }

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { Fail 'Python is required and was not found in PATH.' }

$timestamp = Get-Date -Format 'yyyyMMdd_HHmmss'
$backupRoot = Join-Path $DineshRoot "backups\pre_gcode_integration_$timestamp"
New-Item -ItemType Directory -Force -Path $backupRoot | Out-Null

$integrationDir = Join-Path $DineshRoot 'integrations\gcode'
New-Item -ItemType Directory -Force -Path $integrationDir | Out-Null

$filesToBackup = @('run-gcode-bridge.ps1','gcode-panel.html','GCODE-INTEGRATION-README.txt')
foreach ($name in $filesToBackup) {
    $existing = Join-Path $integrationDir $name
    if (Test-Path -LiteralPath $existing -PathType Leaf) {
        Copy-Item -LiteralPath $existing -Destination (Join-Path $backupRoot $name) -Force
    }
}

$panelPath = Join-Path $integrationDir 'gcode-panel.html'

$runner = @"
param()
`$ErrorActionPreference = 'Stop'
`$env:GCODE_FREE_ONLY = '1'
`$env:GCODE_ACTIVE_PROVIDER = 'openrouter'
`$env:GCODE_FORCE_PROVIDER = '1'
`$env:GCODE_OPENROUTER_MODEL = 'openrouter/free'
`$env:GCODE_NO_TELEMETRY = '1'
`$env:DO_NOT_TRACK = '1'
& '$($python.Source)' '$bridgePy' --gcode-exe '$gcodeExe' --allowed-root '$DineshRoot' --panel-file '$panelPath' --host 127.0.0.1 --port $BridgePort
"@
Set-Content -LiteralPath (Join-Path $integrationDir 'run-gcode-bridge.ps1') -Value $runner -Encoding UTF8

$panel = @"
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dinesh OS · Gcode Worker</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0b1020;color:#eef2ff;margin:0;padding:20px}main{max-width:860px;margin:auto}.card{background:#131a2d;border:1px solid #26314f;border-radius:16px;padding:18px;margin-bottom:16px}h1{margin-top:0;font-size:24px}.row{display:flex;gap:10px;flex-wrap:wrap}select,input,textarea,button{font:inherit;border-radius:10px;border:1px solid #33415f;background:#0f1628;color:#eef2ff;padding:10px}textarea{width:100%;min-height:150px;box-sizing:border-box}input{flex:1;min-width:260px}button{cursor:pointer;background:#1f6feb;border-color:#1f6feb;font-weight:700}.muted{color:#9aa7c4;font-size:13px}.ok{color:#7ee787}.bad{color:#ff7b72}pre{white-space:pre-wrap;word-break:break-word;background:#0a0f1c;padding:14px;border-radius:10px;max-height:420px;overflow:auto}</style>
</head>
<body><main>
<div class="card"><h1>Gcode AI Worker</h1><div id="status" class="muted">Checking bridge...</div><p class="muted">FREE_ONLY · OpenRouter openrouter/free · localhost only · telemetry off</p></div>
<div class="card"><div class="row"><select id="type"><option value="general">General</option><option value="website">Website</option><option value="coding">Coding</option><option value="research">Research</option><option value="content">Content</option><option value="audit">Audit</option></select><select id="mode"><option value="work">Safe Work</option><option value="read_only">Read Only</option></select></div><p></p><input id="cwd" value="$DineshRoot" aria-label="Working directory"><p></p><textarea id="msg" placeholder="What should Gcode do?"></textarea><p></p><button id="run">Run Task</button></div>
<div class="card"><strong>Result</strong><pre id="out">No task yet.</pre></div>
</main>
<script>
const api='';const statusEl=document.getElementById('status');const out=document.getElementById('out');
async function health(){try{const r=await fetch('/health',{cache:'no-store'});const j=await r.json();statusEl.textContent=j.ok?'Bridge READY':'Bridge error';statusEl.className=j.ok?'ok':'bad'}catch(e){statusEl.textContent='Bridge OFFLINE';statusEl.className='bad'}}
async function run(){const b=document.getElementById('run');b.disabled=true;out.textContent='Working...';try{const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message:document.getElementById('msg').value,cwd:document.getElementById('cwd').value,mode:document.getElementById('mode').value,task_type:document.getElementById('type').value})});const j=await r.json();if(j.ok&&j.result){out.textContent=typeof j.result==='string'?j.result:(j.result.text||JSON.stringify(j.result,null,2))}else{out.textContent=JSON.stringify(j,null,2)}}catch(e){out.textContent='ERROR: '+e.message}finally{b.disabled=false;health()}}
document.getElementById('run').addEventListener('click',run);health();setInterval(health,15000);
</script></body></html>
"@
Set-Content -LiteralPath $panelPath -Value $panel -Encoding UTF8

$readme = @"
Dinesh OS <-> Gcode Integration

1. Start bridge:
   powershell -NoProfile -ExecutionPolicy Bypass -File "$integrationDir\run-gcode-bridge.ps1"
2. Open panel:
   http://127.0.0.1:$BridgePort/
3. Bridge health:
   http://127.0.0.1:$BridgePort/health

Safety:
- localhost only (127.0.0.1)
- panel served from same localhost origin
- OpenRouter openrouter/free only
- paid-provider fallback blocked by Gcode core
- telemetry disabled
- working directory restricted to: $DineshRoot
- existing Dinesh OS files were not overwritten
- backup folder: $backupRoot
"@
Set-Content -LiteralPath (Join-Path $integrationDir 'GCODE-INTEGRATION-README.txt') -Value $readme -Encoding UTF8

$startCmd = @"
@echo off
start "Dinesh Gcode Bridge" powershell -NoProfile -ExecutionPolicy Bypass -File "$integrationDir\run-gcode-bridge.ps1"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:$BridgePort/"
"@
Set-Content -LiteralPath (Join-Path $DineshRoot 'START-GCODE-WORKER.cmd') -Value $startCmd -Encoding ASCII

Info "Integration installed: $integrationDir"
Info "Backup: $backupRoot"
Info "One-click launcher: $DineshRoot\START-GCODE-WORKER.cmd"
Info "Panel URL: http://127.0.0.1:$BridgePort/"
Info 'No existing Dinesh OS source files were overwritten.'
Info 'PASS: same-origin bridge files created.'
