#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

MAX_BODY_BYTES = 256 * 1024
MAX_MESSAGE_CHARS = 40_000
DEFAULT_TIMEOUT_SECONDS = 600
RUN_LOCK = threading.BoundedSemaphore(1)
JOBS_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}
ACTIVE_JOB_ID: str | None = None


def json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def next_midnight_utc_ms() -> int:
    now = time.time()
    return int((now - (now % 86400) + 86400) * 1000)


def parse_reset_ms(text: str) -> int:
    for pattern in (
        r'X-RateLimit-Reset[^0-9]{1,40}(\d{10,16})',
        r'x-ratelimit-reset[^0-9]{1,40}(\d{10,16})',
        r'"reset"[^0-9]{1,20}(\d{10,16})',
    ):
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            value = int(match.group(1))
            return value * 1000 if value < 10_000_000_000 else value
    return next_midnight_utc_ms()


def free_daily_quota_error(text: str) -> bool:
    value = text.lower()
    return (
        "429 too many requests" in value
        and (
            "free-models-per-day" in value
            or "openrouter_free_tier_daily" in value
            or "rate limit exceeded" in value
        )
    )


def read_provider_state(path: Path) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {"status": "available", "blocked_until_ms": 0}
    blocked = int(raw.get("blocked_until_ms") or 0)
    if blocked > int(time.time() * 1000):
        return {
            "status": "waiting_for_free_provider",
            "blocked_until_ms": blocked,
            "reason": str(raw.get("reason") or "OpenRouter free daily quota exhausted"),
        }
    if blocked:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    return {"status": "available", "blocked_until_ms": 0}


def write_provider_wait(path: Path, blocked_until_ms: int, reason: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(
        json.dumps(
            {
                "status": "waiting_for_free_provider",
                "blocked_until_ms": int(blocked_until_ms),
                "reason": reason,
                "updated_at_ms": int(time.time() * 1000),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    temp.replace(path)


def task_guard(mode: str, task_type: str) -> str:
    base = (
        "DINESH_OS_POLICY: Work only inside the supplied working directory. "
        "FREE_ONLY is mandatory. Never use a paid model/API as fallback. "
        "Never expose credentials, tokens, cookies, secrets, or private keys. "
        "Do not delete unrelated files. Preserve working features and user data. "
        "Before risky edits create a backup when practical. Run relevant validation after changes. "
        "Never fabricate success, revenue, tests, deployments, or security claims. "
        "Report PASS, FAIL, or NOT TESTED with concise evidence. "
    )
    if mode == "read_only":
        base += "READ_ONLY_MODE: inspect only; do not modify, create, delete, install, deploy, or publish. "
    else:
        base += "SAFE_WORK_MODE: edits are allowed only for this task; keep them minimal and reversible. "
    rules = {
        "website": "WEBSITE_TASK: preserve content/data, test routes/assets, no public deploy unless explicitly requested. ",
        "coding": "CODING_TASK: inspect before editing and run the smallest relevant tests. ",
        "research": "RESEARCH_TASK: separate verified evidence from assumptions. ",
        "content": "CONTENT_TASK: original content only; no misleading claims. ",
        "audit": "AUDIT_TASK: separate verified issues from warnings. ",
        "general": "",
    }
    return base + rules.get(task_type, "")


def stop_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        else:
            process.terminate()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def public_job(job: dict[str, Any]) -> dict[str, Any]:
    started = job.get("started_at")
    finished = job.get("finished_at")
    elapsed = int(max(0, (finished or time.time()) - started)) if started else 0
    return {
        "ok": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "elapsed_seconds": elapsed,
        "timeout_seconds": job["timeout_seconds"],
        "cwd": job["cwd"],
        "task_type": job["task_type"],
        "mode": job["mode"],
        "provider": "openrouter",
        "model": "openrouter/free",
        "result": job.get("result"),
        "error": job.get("error"),
        "exit_code": job.get("exit_code"),
    }


def run_job(server: "BridgeServer", job_id: str, prompt: str, cwd: Path) -> None:
    global ACTIVE_JOB_ID
    with JOBS_LOCK:
        JOBS[job_id]["status"] = "running"
        JOBS[job_id]["started_at"] = time.time()

    env = os.environ.copy()
    env.update(
        {
            "GCODE_FREE_ONLY": "1",
            "GCODE_ACTIVE_PROVIDER": "openrouter",
            "GCODE_FORCE_PROVIDER": "1",
            "GCODE_OPENROUTER_MODEL": "openrouter/free",
            "GCODE_NO_TELEMETRY": "1",
            "DO_NOT_TRACK": "1",
            "GCODE_RUN_AUTO_POKE": "1",
            "GCODE_RUN_AUTO_POKE_MAX_TURNS": "3",
        }
    )
    command = [
        str(server.gcode_exe),
        "--no-update",
        "--no-selfdev",
        "--quiet",
        "--provider",
        "openrouter",
        "--model",
        "openrouter/free",
        "-C",
        str(cwd),
        "run",
        "--json",
        prompt,
    ]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            cwd=str(cwd),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            shell=False,
            creationflags=flags,
        )
        with JOBS_LOCK:
            JOBS[job_id]["process"] = process
        try:
            stdout, stderr = process.communicate(timeout=server.timeout_seconds)
        except subprocess.TimeoutExpired:
            stop_process_tree(process)
            stdout, stderr = process.communicate()
            with JOBS_LOCK:
                job = JOBS[job_id]
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["error"] = "Task cancelled. Partial file changes may exist; inspect before retrying."
                else:
                    job["status"] = "timed_out"
                    job["error"] = f"Task exceeded {server.timeout_seconds} seconds and was stopped."
                job["exit_code"] = process.returncode
                job["finished_at"] = time.time()
            return

        stdout, stderr = stdout.strip(), stderr.strip()
        combined = "\n".join(x for x in (stderr, stdout) if x)
        if process.returncode != 0 and free_daily_quota_error(combined):
            reset_ms = parse_reset_ms(combined)
            reason = "OpenRouter free daily quota exhausted"
            write_provider_wait(server.provider_state_file, reset_ms, reason)
            with JOBS_LOCK:
                job = JOBS[job_id]
                job["status"] = "waiting_for_free_provider"
                job["error"] = reason
                job["provider_reset_ms"] = reset_ms
                job["exit_code"] = process.returncode
                job["finished_at"] = time.time()
            return

        try:
            parsed: Any = json.loads(stdout) if stdout else None
        except json.JSONDecodeError:
            parsed = None
        with JOBS_LOCK:
            job = JOBS[job_id]
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["error"] = "Task cancelled. Partial file changes may exist; inspect before retrying."
            elif process.returncode == 0:
                job["status"] = "done"
                job["result"] = parsed if parsed is not None else stdout
            else:
                job["status"] = "failed"
                job["error"] = stderr or stdout or "Gcode run failed"
            job["exit_code"] = process.returncode
            job["finished_at"] = time.time()
    except Exception as exc:
        with JOBS_LOCK:
            JOBS[job_id]["status"] = "failed"
            JOBS[job_id]["error"] = f"Bridge error: {exc}"
            JOBS[job_id]["finished_at"] = time.time()
    finally:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["process"] = None
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None
        RUN_LOCK.release()


PANEL_HTML = r'''<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Dinesh OS · Gcode Worker</title>
<style>
body{font-family:system-ui,Segoe UI,Arial,sans-serif;background:#0b1020;color:#eef2ff;margin:0;padding:20px}main{max-width:900px;margin:auto}.card{background:#131a2d;border:1px solid #26314f;border-radius:16px;padding:18px;margin-bottom:16px}.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center}select,input,textarea,button{font:inherit;border-radius:10px;border:1px solid #33415f;background:#0f1628;color:#eef2ff;padding:10px}textarea{width:100%;min-height:150px;box-sizing:border-box}input{flex:1;min-width:260px}button{cursor:pointer;background:#1f6feb;border-color:#1f6feb;font-weight:700}button.danger{background:#7a1f2b;border-color:#a32c3a}.ok{color:#7ee787}.bad{color:#ff7b72}.warn{color:#e3b341}.muted{color:#9aa7c4;font-size:13px}.hidden{display:none}pre{white-space:pre-wrap;word-break:break-word;background:#0a0f1c;padding:14px;border-radius:10px;max-height:480px;overflow:auto}.pill{display:inline-block;border:1px solid #33415f;border-radius:999px;padding:5px 9px;font-size:12px}
</style></head><body><main>
<div class="card"><h2>Gcode AI Worker</h2><div id="status" class="muted">Checking bridge...</div><p><span class="pill">FREE_ONLY</span> <span class="pill">openrouter/free</span> <span class="pill">localhost</span> <span class="pill">telemetry off</span></p></div>
<div class="card"><div class="row"><select id="type"><option>general</option><option>website</option><option>coding</option><option>research</option><option>content</option><option>audit</option></select><select id="mode"><option value="work">Safe Work</option><option value="read_only">Read Only</option></select></div><p><input id="cwd" value="__ROOT__"></p><textarea id="msg" placeholder="What should Gcode do?"></textarea><p class="row"><button id="run">Run Task</button><button id="cancel" class="danger hidden">Cancel Task</button></p></div>
<div class="card"><strong>Task status</strong><div id="job" class="muted">No task running.</div><div id="elapsed" class="muted"></div></div>
<div class="card"><strong>Result</strong><pre id="out">No task yet.</pre></div>
<script>
const statusEl=document.getElementById('status'),jobEl=document.getElementById('job'),elapsedEl=document.getElementById('elapsed'),out=document.getElementById('out'),runBtn=document.getElementById('run'),cancelBtn=document.getElementById('cancel');let currentJob=null,pollTimer=null;
function fmt(s){s=Math.max(0,Math.floor(s));const m=Math.floor(s/60),r=s%60;return m?`${m}m ${r}s`:`${r}s`}
function resetText(ms){return ms?new Date(ms).toLocaleString():''}
function setBusy(v){runBtn.disabled=v;cancelBtn.classList.toggle('hidden',!v)}
async function health(){try{const r=await fetch('/health',{cache:'no-store'}),j=await r.json();if(j.provider_status==='waiting_for_free_provider'){statusEl.textContent='WAITING_FOR_FREE_PROVIDER · free quota resets '+resetText(j.provider_reset_ms);statusEl.className='warn';runBtn.disabled=true}else{statusEl.textContent=j.busy?'Bridge READY · worker busy':'Bridge READY · worker idle';statusEl.className=j.busy?'warn':'ok';if(!currentJob)runBtn.disabled=false}if(j.active_job_id&&!currentJob){currentJob=j.active_job_id;setBusy(true);startPolling()}}catch(e){statusEl.textContent='Bridge OFFLINE';statusEl.className='bad'}}
async function poll(){if(!currentJob)return;const r=await fetch('/jobs/'+currentJob,{cache:'no-store'}),j=await r.json();if(!j.ok)return;jobEl.textContent=`${j.status.toUpperCase()} · ${j.task_type} · ${j.mode}`;elapsedEl.textContent=`Elapsed: ${fmt(j.elapsed_seconds||0)} · timeout: ${fmt(j.timeout_seconds||600)}`;if(['queued','running'].includes(j.status))return;clearInterval(pollTimer);pollTimer=null;setBusy(false);if(j.result)out.textContent=typeof j.result==='string'?j.result:(j.result.text||JSON.stringify(j.result,null,2));else out.textContent='ERROR: '+(j.error||j.status);currentJob=null;health()}
function startPolling(){if(pollTimer)clearInterval(pollTimer);poll();pollTimer=setInterval(poll,2000)}
async function run(){const body={message:document.getElementById('msg').value.trim(),cwd:document.getElementById('cwd').value,mode:document.getElementById('mode').value,task_type:document.getElementById('type').value};if(!body.message){out.textContent='ERROR: Task message is empty.';return}setBusy(true);out.textContent='Submitting task...';const r=await fetch('/run',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});const j=await r.json();if(!r.ok||!j.ok){setBusy(false);if(j.status==='waiting_for_free_provider')out.textContent='WAITING_FOR_FREE_PROVIDER · reset '+resetText(j.provider_reset_ms);else out.textContent='ERROR: '+(j.error||'Task start failed');health();return}currentJob=j.job_id;startPolling()}
async function cancel(){if(!currentJob)return;await fetch('/jobs/'+currentJob+'/cancel',{method:'POST'});out.textContent='Cancellation requested...';startPolling()}
runBtn.onclick=run;cancelBtn.onclick=cancel;health();setInterval(health,15000);
</script></main></body></html>'''


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, address, handler_cls, *, gcode_exe: Path, allowed_roots: list[Path], provider_state_file: Path, timeout_seconds: int):
        super().__init__(address, handler_cls)
        self.gcode_exe = gcode_exe
        self.allowed_roots = allowed_roots
        self.provider_state_file = provider_state_file
        self.timeout_seconds = timeout_seconds


class Handler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dinesh-gcode-bridge] {fmt % args}")

    def send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        clean = self.path.split("?", 1)[0].rstrip("/")
        if clean in {"", "/index.html"}:
            body = PANEL_HTML.replace("__ROOT__", str(self.server.allowed_roots[0])).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'")
            self.end_headers()
            self.wfile.write(body)
            return
        if clean == "/health":
            state = read_provider_state(self.server.provider_state_file)
            with JOBS_LOCK:
                active = ACTIVE_JOB_ID
            self.send_json(200, {
                "ok": True,
                "busy": active is not None,
                "active_job_id": active,
                "provider": "openrouter",
                "model": "openrouter/free",
                "free_only": True,
                "provider_status": state["status"],
                "provider_reset_ms": state.get("blocked_until_ms", 0),
                "provider_reason": state.get("reason"),
            })
            return
        if clean.startswith("/jobs/"):
            job_id = clean[len("/jobs/"):]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = public_job(job) if job else None
            if payload is None:
                self.send_json(404, {"ok": False, "error": "Job not found"})
            else:
                self.send_json(200, payload)
            return
        self.send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        global ACTIVE_JOB_ID
        clean = self.path.split("?", 1)[0].rstrip("/")
        if clean.startswith("/jobs/") and clean.endswith("/cancel"):
            job_id = clean[len("/jobs/"):-len("/cancel")].rstrip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self.send_json(404, {"ok": False, "error": "Job not found"})
                    return
                job["cancel_requested"] = True
                process = job.get("process")
            if process is not None:
                stop_process_tree(process)
            self.send_json(202, {"ok": True, "status": "cancelling"})
            return
        if clean != "/run":
            self.send_json(404, {"ok": False, "error": "Not found"})
            return

        state = read_provider_state(self.server.provider_state_file)
        if state["status"] == "waiting_for_free_provider":
            self.send_json(503, {
                "ok": False,
                "status": "waiting_for_free_provider",
                "error": state.get("reason"),
                "provider_reset_ms": state.get("blocked_until_ms", 0),
            })
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self.send_json(400, {"ok": False, "error": "Invalid Content-Length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self.send_json(413, {"ok": False, "error": "Request body invalid or too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self.send_json(400, {"ok": False, "error": "Invalid JSON"})
            return

        message = str(payload.get("message", "")).strip()
        if not message or len(message) > MAX_MESSAGE_CHARS:
            self.send_json(400, {"ok": False, "error": "Invalid task message"})
            return
        mode = str(payload.get("mode", "work")).lower()
        if mode not in {"work", "read_only"}:
            mode = "work"
        task_type = str(payload.get("task_type", "general")).lower()
        if task_type not in {"general", "website", "coding", "research", "content", "audit"}:
            task_type = "general"
        requested = str(payload.get("cwd", "")).strip()
        try:
            cwd = Path(requested).expanduser().resolve() if requested else self.server.allowed_roots[0]
        except OSError:
            self.send_json(400, {"ok": False, "error": "Invalid working directory"})
            return
        if not cwd.is_dir() or not any(is_within(cwd, root) for root in self.server.allowed_roots):
            self.send_json(403, {"ok": False, "error": "Working directory is outside allowed roots"})
            return
        if not RUN_LOCK.acquire(blocking=False):
            self.send_json(429, {"ok": False, "error": "Worker is busy"})
            return

        job_id = uuid.uuid4().hex[:12]
        prompt = task_guard(mode, task_type) + "\n\nUSER_TASK:\n" + message
        with JOBS_LOCK:
            JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "cwd": str(cwd),
                "task_type": task_type,
                "mode": mode,
                "timeout_seconds": self.server.timeout_seconds,
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "exit_code": None,
                "cancel_requested": False,
                "process": None,
            }
            ACTIVE_JOB_ID = job_id
        threading.Thread(target=run_job, args=(self.server, job_id, prompt, cwd), daemon=True).start()
        self.send_json(202, {"ok": True, "job_id": job_id, "status": "queued", "timeout_seconds": self.server.timeout_seconds})


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser()
    p.add_argument("--gcode-exe", required=True)
    p.add_argument("--allowed-root", action="append", required=True)
    p.add_argument("--provider-state-file", required=True)
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8855)
    p.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing non-local bind")
    gcode_exe = Path(args.gcode_exe).resolve()
    if not gcode_exe.is_file():
        raise SystemExit(f"gcode.exe not found: {gcode_exe}")
    roots = [Path(x).resolve() for x in args.allowed_root]
    if any(not x.is_dir() for x in roots):
        raise SystemExit("Allowed root missing")
    state_file = Path(args.provider_state_file).resolve()
    server = BridgeServer(("127.0.0.1", args.port), Handler, gcode_exe=gcode_exe, allowed_roots=roots, provider_state_file=state_file, timeout_seconds=max(30, args.timeout))
    print(f"[dinesh-gcode-bridge] READY http://127.0.0.1:{args.port}/")
    print("[dinesh-gcode-bridge] FREE_ONLY=openrouter/free | provider-gate=on | telemetry=off")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
