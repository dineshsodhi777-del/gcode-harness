#!/usr/bin/env python3
"""Local-only Dinesh OS -> Gcode bridge.

Zero third-party dependencies. Binds only to 127.0.0.1, serves the worker panel
from the same origin, and runs Gcode tasks as observable background jobs with
fail-closed FREE_ONLY environment settings.
"""

from __future__ import annotations

import argparse
import json
import os
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
ALLOWED_ORIGINS = {
    "http://127.0.0.1:8844",
    "http://localhost:8844",
    "http://127.0.0.1:8855",
    "http://localhost:8855",
}
RUN_LOCK = threading.BoundedSemaphore(1)
JOBS_LOCK = threading.Lock()
JOBS: dict[str, dict[str, Any]] = {}
ACTIVE_JOB_ID: str | None = None


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _json_bytes(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _task_guard(mode: str, task_type: str) -> str:
    base = (
        "DINESH_OS_POLICY: Work only inside the supplied working directory. "
        "FREE_ONLY is enforced by runtime: never use or suggest a paid model/API as an automatic fallback. "
        "Never expose credentials, tokens, cookies, secrets, or private keys. "
        "Do not delete unrelated files. Preserve working features and user data. "
        "Before risky edits, create a backup when practical. Run relevant validation/tests after changes. "
        "Never fabricate success, revenue, test results, deployments, or security claims. "
        "Report final status using PASS, FAIL, or NOT TESTED with concise evidence. "
    )
    if mode == "read_only":
        base += "READ_ONLY_MODE: inspect only; do not modify, create, delete, install, deploy, publish, or execute destructive commands. "
    else:
        base += "SAFE_WORK_MODE: you may edit files needed for the requested task, but keep scope minimal and reversible. "

    task_rules = {
        "website": "WEBSITE_TASK: preserve existing content/data, test routes/assets, and avoid public deployment unless explicitly requested. ",
        "coding": "CODING_TASK: inspect before editing, keep changes minimal, and run the smallest relevant tests. ",
        "research": "RESEARCH_TASK: distinguish verified evidence from assumptions and do not present guesses as facts. ",
        "content": "CONTENT_TASK: produce original content and avoid copyrighted text replication or misleading claims. ",
        "audit": "AUDIT_TASK: inspect first, separate verified issues from warnings, and do not fix unless the user requested work mode. ",
        "general": "",
    }
    return base + task_rules.get(task_type, "")


def _job_public(job: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    started_at = job.get("started_at")
    finished_at = job.get("finished_at")
    if started_at:
        end = finished_at or now
        elapsed_seconds = max(0, int(end - started_at))
    else:
        elapsed_seconds = 0
    return {
        "ok": True,
        "job_id": job["job_id"],
        "status": job["status"],
        "elapsed_seconds": elapsed_seconds,
        "timeout_seconds": job["timeout_seconds"],
        "cwd": job["cwd"],
        "task_type": job["task_type"],
        "mode": job["mode"],
        "provider": "openrouter",
        "model": "openrouter/free",
        "result": job.get("result"),
        "error": job.get("error"),
        "exit_code": job.get("exit_code"),
        "cancel_requested": bool(job.get("cancel_requested")),
    }


def _stop_process_tree(process: subprocess.Popen[str]) -> None:
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
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def _run_job(server: "BridgeServer", job_id: str, guarded_message: str, cwd: Path) -> None:
    global ACTIVE_JOB_ID
    with JOBS_LOCK:
        job = JOBS[job_id]
        job["status"] = "running"
        job["started_at"] = time.time()

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
        str(server.gcode_exe), "--no-update", "--no-selfdev", "--quiet",
        "--provider", "openrouter", "--model", "openrouter/free", "-C", str(cwd),
        "run", "--json", guarded_message,
    ]
    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
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
            creationflags=creationflags,
        )
        with JOBS_LOCK:
            JOBS[job_id]["process"] = process

        try:
            stdout, stderr = process.communicate(timeout=server.timeout_seconds)
        except subprocess.TimeoutExpired:
            _stop_process_tree(process)
            stdout, stderr = process.communicate()
            with JOBS_LOCK:
                job = JOBS[job_id]
                if job.get("cancel_requested"):
                    job["status"] = "cancelled"
                    job["error"] = "Task cancelled by user. Partial file changes may exist; inspect before retrying."
                else:
                    job["status"] = "timed_out"
                    job["error"] = f"Task exceeded {server.timeout_seconds} seconds and was stopped."
                job["stdout"] = stdout.strip()
                job["stderr"] = stderr.strip()
                job["exit_code"] = process.returncode
                job["finished_at"] = time.time()
            return

        stdout = stdout.strip()
        stderr = stderr.strip()
        try:
            parsed: Any = json.loads(stdout) if stdout else None
        except json.JSONDecodeError:
            parsed = None

        with JOBS_LOCK:
            job = JOBS[job_id]
            if job.get("cancel_requested"):
                job["status"] = "cancelled"
                job["error"] = "Task cancelled by user. Partial file changes may exist; inspect before retrying."
            elif process.returncode == 0:
                job["status"] = "done"
                job["result"] = parsed if parsed is not None else stdout
                if stderr:
                    job["stderr"] = stderr
            else:
                job["status"] = "failed"
                job["error"] = stderr or stdout or "Gcode run failed"
            job["exit_code"] = process.returncode
            job["finished_at"] = time.time()
    except Exception as exc:
        with JOBS_LOCK:
            job = JOBS[job_id]
            job["status"] = "failed"
            job["error"] = f"Bridge error: {exc}"
            job["finished_at"] = time.time()
    finally:
        with JOBS_LOCK:
            if job_id in JOBS:
                JOBS[job_id]["process"] = None
            if ACTIVE_JOB_ID == job_id:
                ACTIVE_JOB_ID = None
        RUN_LOCK.release()


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        gcode_exe: Path,
        allowed_roots: list[Path],
        panel_file: Path,
        timeout_seconds: int,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.gcode_exe = gcode_exe
        self.allowed_roots = allowed_roots
        self.panel_file = panel_file
        self.timeout_seconds = timeout_seconds


class Handler(BaseHTTPRequestHandler):
    server: BridgeServer

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[dinesh-gcode-bridge] {self.address_string()} - {fmt % args}")

    def _origin_allowed(self) -> bool:
        origin = self.headers.get("Origin")
        return origin is None or origin in ALLOWED_ORIGINS

    def _cors_headers(self) -> None:
        origin = self.headers.get("Origin")
        if origin in ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = _json_bytes(payload)
        self.send_response(status)
        self._cors_headers()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _send_panel(self) -> None:
        try:
            body = self.server.panel_file.read_bytes()
        except OSError as exc:
            self._send_json(500, {"ok": False, "error": f"Panel file unavailable: {exc}"})
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin not allowed"})
            return
        self.send_response(204)
        self._cors_headers()
        self.end_headers()

    def do_GET(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin not allowed"})
            return
        clean = self.path.split("?", 1)[0].rstrip("/")
        if clean in {"", "/index.html"}:
            self._send_panel()
            return
        if clean == "/health":
            with JOBS_LOCK:
                active = ACTIVE_JOB_ID
            self._send_json(
                200,
                {
                    "ok": True,
                    "service": "dinesh-os-gcode-bridge",
                    "bind": "127.0.0.1",
                    "provider": "openrouter",
                    "model": "openrouter/free",
                    "free_only": True,
                    "telemetry": "off",
                    "busy": active is not None,
                    "active_job_id": active,
                    "allowed_roots": [str(root) for root in self.server.allowed_roots],
                },
            )
            return
        if clean.startswith("/jobs/"):
            job_id = clean[len("/jobs/"):]
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                payload = _job_public(job) if job else None
            if payload is None:
                self._send_json(404, {"ok": False, "error": "Job not found"})
            else:
                self._send_json(200, payload)
            return
        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        global ACTIVE_JOB_ID
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin not allowed"})
            return
        clean = self.path.split("?", 1)[0].rstrip("/")

        if clean.startswith("/jobs/") and clean.endswith("/cancel"):
            job_id = clean[len("/jobs/"):-len("/cancel")].rstrip("/")
            with JOBS_LOCK:
                job = JOBS.get(job_id)
                if not job:
                    self._send_json(404, {"ok": False, "error": "Job not found"})
                    return
                if job["status"] not in {"queued", "running"}:
                    self._send_json(409, {"ok": False, "error": f"Job is already {job['status']}"})
                    return
                job["cancel_requested"] = True
                process = job.get("process")
            if process is not None:
                _stop_process_tree(process)
            self._send_json(202, {"ok": True, "job_id": job_id, "status": "cancelling"})
            return

        if clean != "/run":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        try:
            length = int(self.headers.get("Content-Length") or "0")
        except ValueError:
            self._send_json(400, {"ok": False, "error": "Invalid Content-Length"})
            return
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send_json(413, {"ok": False, "error": "Request body is empty or too large"})
            return
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            self._send_json(400, {"ok": False, "error": "Invalid JSON"})
            return

        message = str(payload.get("message", "")).strip()
        if not message:
            self._send_json(400, {"ok": False, "error": "message is required"})
            return
        if len(message) > MAX_MESSAGE_CHARS:
            self._send_json(413, {"ok": False, "error": "message is too long"})
            return

        mode = str(payload.get("mode", "work")).strip().lower()
        if mode not in {"work", "read_only"}:
            self._send_json(400, {"ok": False, "error": "mode must be work or read_only"})
            return

        task_type = str(payload.get("task_type", "general")).strip().lower()
        if task_type not in {"general", "website", "coding", "research", "content", "audit"}:
            task_type = "general"

        requested_cwd = str(payload.get("cwd", "")).strip()
        try:
            cwd = Path(requested_cwd).expanduser().resolve() if requested_cwd else self.server.allowed_roots[0]
        except OSError:
            self._send_json(400, {"ok": False, "error": "Invalid working directory"})
            return
        if not cwd.is_dir() or not any(_is_within(cwd, root) for root in self.server.allowed_roots):
            self._send_json(403, {"ok": False, "error": "Working directory is outside allowed roots"})
            return

        if not RUN_LOCK.acquire(blocking=False):
            with JOBS_LOCK:
                active = ACTIVE_JOB_ID
            self._send_json(429, {"ok": False, "error": "Gcode worker is busy.", "active_job_id": active})
            return

        job_id = uuid.uuid4().hex[:12]
        guarded_message = _task_guard(mode, task_type) + "\n\nUSER_TASK:\n" + message
        with JOBS_LOCK:
            JOBS[job_id] = {
                "job_id": job_id,
                "status": "queued",
                "cwd": str(cwd),
                "task_type": task_type,
                "mode": mode,
                "timeout_seconds": self.server.timeout_seconds,
                "created_at": time.time(),
                "started_at": None,
                "finished_at": None,
                "result": None,
                "error": None,
                "exit_code": None,
                "cancel_requested": False,
                "process": None,
            }
            ACTIVE_JOB_ID = job_id
        thread = threading.Thread(
            target=_run_job,
            args=(self.server, job_id, guarded_message, cwd),
            daemon=True,
            name=f"gcode-job-{job_id}",
        )
        thread.start()
        self._send_json(202, {
            "ok": True,
            "job_id": job_id,
            "status": "queued",
            "timeout_seconds": self.server.timeout_seconds,
        })


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Dinesh OS to Gcode bridge")
    parser.add_argument("--gcode-exe", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--panel-file", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8855)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing non-local bind. Use 127.0.0.1 only.")

    gcode_exe = Path(args.gcode_exe).expanduser().resolve()
    panel_file = Path(args.panel_file).expanduser().resolve()
    if not gcode_exe.is_file():
        raise SystemExit(f"gcode.exe not found: {gcode_exe}")
    if not panel_file.is_file():
        raise SystemExit(f"Panel file not found: {panel_file}")

    allowed_roots: list[Path] = []
    for raw in args.allowed_root:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"Allowed root does not exist: {root}")
        allowed_roots.append(root)

    server = BridgeServer(
        ("127.0.0.1", args.port), Handler,
        gcode_exe=gcode_exe,
        allowed_roots=allowed_roots,
        panel_file=panel_file,
        timeout_seconds=max(30, args.timeout),
    )
    print(f"[dinesh-gcode-bridge] READY http://127.0.0.1:{args.port}/")
    print("[dinesh-gcode-bridge] FREE_ONLY=openrouter/free | telemetry=off | local-only | async-jobs=on")
    for root in allowed_roots:
        print(f"[dinesh-gcode-bridge] Allowed root: {root}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        with JOBS_LOCK:
            active_id = ACTIVE_JOB_ID
            process = JOBS.get(active_id, {}).get("process") if active_id else None
        if process is not None:
            _stop_process_tree(process)
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
