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

$filesToBackup = @('run-gcode-bridge.ps1','restart-gcode-bridge.ps1','gcode-panel.html','GCODE-INTEGRATION-README.txt')
foreach ($name in $filesToBackup) {
    $existing = Join-Path $integrationDir $name
    if (Test-Path -LiteralPath $existing -PathType Leaf) {
        Copy-Item -LiteralPath $existing -Destination (Join-Path $backupRoot $name) -Force
    }
}

$panelPath = Join-Path $integrationDir 'gcode-panel.html'
$runnerPath = Join-Path $integrationDir 'run-gcode-bridge.ps1'
$restartPath = Join-Path $integrationDir 'restart-gcode-bridge.ps1'

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
Set-Content -LiteralPath $runnerPath -Value $runner -Encoding UTF8

$restart = @"
param()
`$ErrorActionPreference = 'Stop'
`$bridgeScript = '$bridgePy'
`$processes = @(Get-CimInstance Win32_Process)

function Stop-ProcessTree([uint32]`$ProcessId) {
    `$children = @(`$processes | Where-Object { `$_.ParentProcessId -eq `$ProcessId })
    foreach (`$child in `$children) {
        Stop-ProcessTree -ProcessId ([uint32]`$child.ProcessId)
    }
    Stop-Process -Id `$ProcessId -Force -ErrorAction SilentlyContinue
}

`$bridgeProcesses = @(`$processes | Where-Object {
    `$_.Name -match '^python(w)?\.exe$' -and
    `$_.CommandLine -and
    `$_.CommandLine.IndexOf(`$bridgeScript, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
})

foreach (`$process in `$bridgeProcesses) {
    Stop-ProcessTree -ProcessId ([uint32]`$process.ProcessId)
}
if (`$bridgeProcesses.Count -gt 0) { Start-Sleep -Milliseconds 800 }

Start-Process -FilePath 'powershell.exe' -ArgumentList @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', '"$runnerPath"'
)
"@
Set-Content -LiteralPath $restartPath -Value $restart -Encoding UTF8

# Keep the HTML/JavaScript literal. A double-quoted PowerShell here-string would
# interpret JavaScript template expressions such as ${r} as PowerShell variables.
$panel = @'
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dinesh OS · Gcode Worker</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0b1020;color:#eef2ff;margin:0;padding:20px}main{max-width:900px;margin:auto}.card{background:#131a2d;border:1px solid #26314f;border-radius:16px;padding:18px;margin-bottom:16px}h1{margin-top:0;font-size:24px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}select,input,textarea,button{font:inherit;border-radius:10px;border:1px solid #33415f;background:#0f1628;color:#eef2ff;padding:10px}textarea{width:100%;min-height:150px;box-sizing:border-box}input{flex:1;min-width:260px}button{cursor:pointer;background:#1f6feb;border-color:#1f6feb;font-weight:700}button.danger{background:#7a1f2b;border-color:#a32c3a}button:disabled{opacity:.55;cursor:not-allowed}.muted{color:#9aa7c4;font-size:13px}.ok{color:#7ee787}.bad{color:#ff7b72}.warn{color:#e3b341}.pill{display:inline-block;border:1px solid #33415f;border-radius:999px;padding:5px 9px;font-size:12px}.hidden{display:none}pre{white-space:pre-wrap;word-break:break-word;background:#0a0f1c;padding:14px;border-radius:10px;max-height:480px;overflow:auto}.statusline{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin-top:10px}</style>
</head>
<body><main>
<div class="card"><h1>Gcode AI Worker</h1><div id="status" class="muted">Checking bridge...</div><div class="statusline"><span class="pill">FREE_ONLY</span><span class="pill">OpenRouter openrouter/free</span><span class="pill">localhost only</span><span class="pill">telemetry off</span></div></div>
<div class="card"><div class="row"><select id="type"><option value="general">General</option><option value="website">Website</option><option value="coding">Coding</option><option value="research">Research</option><option value="content">Content</option><option value="audit">Audit</option></select><select id="mode"><option value="work">Safe Work</option><option value="read_only">Read Only</option></select></div><p></p><input id="cwd" value="__DINESH_ROOT__" aria-label="Working directory"><p></p><textarea id="msg" placeholder="What should Gcode do?"></textarea><p></p><div class="row"><button id="run">Run Task</button><button id="cancel" class="danger hidden">Cancel Task</button></div></div>
<div class="card"><strong>Task status</strong><div id="job" class="muted">No task running.</div><div id="elapsed" class="muted"></div></div>
<div class="card"><strong>Result</strong><pre id="out">No task yet.</pre></div>
</main>
<script>
const statusEl=document.getElementById('status'),out=document.getElementById('out'),jobEl=document.getElementById('job'),elapsedEl=document.getElementById('elapsed'),runBtn=document.getElementById('run'),cancelBtn=document.getElementById('cancel');
let currentJobId=null,pollTimer=null,elapsedTimer=null,currentStartedMs=0,currentTimeout=600;
function fmt(s){s=Math.max(0,Math.floor(s));const m=Math.floor(s/60),r=s%60;return m?`${m}m ${r}s`:`${r}s`}
function setRunningUI(running){runBtn.disabled=running;cancelBtn.classList.toggle('hidden',!running);cancelBtn.disabled=!running}
function stopTimers(){if(pollTimer){clearInterval(pollTimer);pollTimer=null}if(elapsedTimer){clearInterval(elapsedTimer);elapsedTimer=null}}
function startElapsed(){if(elapsedTimer)clearInterval(elapsedTimer);elapsedTimer=setInterval(()=>{if(!currentStartedMs)return;const e=(Date.now()-currentStartedMs)/1000;elapsedEl.textContent=`Elapsed: ${fmt(e)} · safety timeout: ${fmt(currentTimeout)}`},1000)}
function renderResult(j){if(j.result){out.textContent=typeof j.result==='string'?j.result:(j.result.text||JSON.stringify(j.result,null,2))}else if(j.error){out.textContent='ERROR: '+j.error}else{out.textContent=JSON.stringify(j,null,2)}}
async function health(){try{const r=await fetch('/health',{cache:'no-store'}),j=await r.json();if(!j.ok)throw new Error(j.error||'Bridge error');statusEl.textContent=j.busy?'Bridge READY · worker busy':'Bridge READY · worker idle';statusEl.className=j.busy?'warn':'ok';if(j.active_job_id&&!currentJobId){currentJobId=j.active_job_id;setRunningUI(true);jobEl.textContent='Reconnected to active task: '+currentJobId;currentStartedMs=Date.now();startElapsed();startPolling()}}catch(e){statusEl.textContent='Bridge OFFLINE';statusEl.className='bad'}}
async function poll(){if(!currentJobId)return;try{const r=await fetch('/jobs/'+encodeURIComponent(currentJobId),{cache:'no-store'}),j=await r.json();if(!r.ok||!j.ok)throw new Error(j.error||'Job status failed');currentTimeout=j.timeout_seconds||600;jobEl.textContent=`${j.status.toUpperCase()} · ${j.task_type} · ${j.mode}`;elapsedEl.textContent=`Elapsed: ${fmt(j.elapsed_seconds||0)} · safety timeout: ${fmt(currentTimeout)}`;if(j.status==='running'||j.status==='queued'){if(!currentStartedMs)currentStartedMs=Date.now()-(j.elapsed_seconds||0)*1000;return}stopTimers();setRunningUI(false);renderResult(j);currentJobId=null;currentStartedMs=0;health()}catch(e){jobEl.textContent='Status check error: '+e.message;jobEl.className='bad'}}
function startPolling(){if(pollTimer)clearInterval(pollTimer);poll();pollTimer=setInterval(poll,2000)}
async function run(){const message=document.getElementById('msg').value.trim();if(!message){out.textContent='ERROR: Task message is empty.';return}setRunningUI(true);out.textContent='Task accepted. Waiting for Gcode result...';jobEl.className='muted';jobEl.textContent='Submitting task...';elapsedEl.textContent='';try{const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({message,cwd:document.getElementById('cwd').value,mode:document.getElementById('mode').value,task_type:document.getElementById('type').value})}),j=await r.json();if(!r.ok||!j.ok){throw new Error(j.error||'Task start failed')}currentJobId=j.job_id;currentTimeout=j.timeout_seconds||600;currentStartedMs=Date.now();jobEl.textContent='QUEUED · Job '+currentJobId;startElapsed();startPolling()}catch(e){setRunningUI(false);out.textContent='ERROR: '+e.message;jobEl.textContent='Task did not start.';health()}}
async function cancel(){if(!currentJobId)return;cancelBtn.disabled=true;jobEl.textContent='Cancelling task...';try{const r=await fetch('/jobs/'+encodeURIComponent(currentJobId)+'/cancel',{method:'POST'}),j=await r.json();if(!r.ok&&!j.ok)throw new Error(j.error||'Cancel failed');out.textContent='Cancellation requested. Waiting for worker to stop safely...';startPolling()}catch(e){cancelBtn.disabled=false;out.textContent='ERROR: '+e.message}}
runBtn.addEventListener('click',run);cancelBtn.addEventListener('click',cancel);health();setInterval(health,15000);
</script></body></html>
'@
$panel = $panel.Replace('__DINESH_ROOT__', [System.Net.WebUtility]::HtmlEncode($DineshRoot))
Set-Content -LiteralPath $panelPath -Value $panel -Encoding UTF8

$readme = @"
Dinesh OS <-> Gcode Integration

1. Start/restart bridge:
   powershell -NoProfile -ExecutionPolicy Bypass -File "$restartPath"
2. Open panel:
   http://127.0.0.1:$BridgePort/
3. Bridge health:
   http://127.0.0.1:$BridgePort/health

Worker behavior:
- tasks run as background jobs
- browser polls status every 2 seconds
- elapsed time and safety timeout are visible
- Cancel Task terminates only the Gcode process tree for that bridge job
- if a task is cancelled during file edits, partial changes may exist and should be inspected before retrying

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
powershell -NoProfile -ExecutionPolicy Bypass -File "$restartPath"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:$BridgePort/"
"@
Set-Content -LiteralPath (Join-Path $DineshRoot 'START-GCODE-WORKER.cmd') -Value $startCmd -Encoding ASCII

Info "Integration installed: $integrationDir"
Info "Backup: $backupRoot"
Info "One-click launcher: $DineshRoot\START-GCODE-WORKER.cmd"
Info "Panel URL: http://127.0.0.1:$BridgePort/"
Info 'No existing Dinesh OS source files were overwritten.'
Info 'PASS: observable background-job bridge files created.'
