#!/usr/bin/env python3
"""Local-only Dinesh OS -> Gcode bridge.

Zero third-party dependencies. Binds only to 127.0.0.1 and invokes Gcode's
non-interactive `run --json` command with fail-closed FREE_ONLY environment
settings.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time
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


class BridgeServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        handler_cls: type[BaseHTTPRequestHandler],
        *,
        gcode_exe: Path,
        allowed_roots: list[Path],
        timeout_seconds: int,
    ) -> None:
        super().__init__(server_address, handler_cls)
        self.gcode_exe = gcode_exe
        self.allowed_roots = allowed_roots
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
        if self.path.rstrip("/") == "/health":
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
                    "busy": RUN_LOCK._value == 0,  # status only; admission still uses semaphore
                    "allowed_roots": [str(root) for root in self.server.allowed_roots],
                },
            )
            return
        self._send_json(404, {"ok": False, "error": "Not found"})

    def do_POST(self) -> None:
        if not self._origin_allowed():
            self._send_json(403, {"ok": False, "error": "Origin not allowed"})
            return
        if self.path.rstrip("/") != "/run":
            self._send_json(404, {"ok": False, "error": "Not found"})
            return

        content_length = self.headers.get("Content-Length")
        try:
            length = int(content_length or "0")
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
        if requested_cwd:
            try:
                cwd = Path(requested_cwd).expanduser().resolve()
            except OSError:
                self._send_json(400, {"ok": False, "error": "Invalid working directory"})
                return
        else:
            cwd = self.server.allowed_roots[0]

        if not cwd.is_dir() or not any(_is_within(cwd, root) for root in self.server.allowed_roots):
            self._send_json(403, {"ok": False, "error": "Working directory is outside allowed roots"})
            return

        if not RUN_LOCK.acquire(blocking=False):
            self._send_json(429, {"ok": False, "error": "Gcode worker is busy. Wait for the current task to finish."})
            return

        started = time.monotonic()
        try:
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
                    "GCODE_RUN_AUTO_POKE_MAX_TURNS": "6",
                }
            )

            guarded_message = _task_guard(mode, task_type) + "\n\nUSER_TASK:\n" + message
            command = [
                str(self.server.gcode_exe),
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
                guarded_message,
            ]
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0
            completed = subprocess.run(
                command,
                cwd=str(cwd),
                env=env,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.server.timeout_seconds,
                shell=False,
                creationflags=creationflags,
            )
            duration_ms = int((time.monotonic() - started) * 1000)
            stdout = completed.stdout.strip()
            stderr = completed.stderr.strip()
            parsed: Any = None
            if stdout:
                try:
                    parsed = json.loads(stdout)
                except json.JSONDecodeError:
                    parsed = None

            if completed.returncode == 0:
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "duration_ms": duration_ms,
                        "cwd": str(cwd),
                        "provider": "openrouter",
                        "model": "openrouter/free",
                        "result": parsed if parsed is not None else stdout,
                        "stderr": stderr,
                    },
                )
            else:
                self._send_json(
                    502,
                    {
                        "ok": False,
                        "duration_ms": duration_ms,
                        "exit_code": completed.returncode,
                        "error": stderr or stdout or "Gcode run failed",
                    },
                )
        except subprocess.TimeoutExpired:
            self._send_json(
                504,
                {
                    "ok": False,
                    "error": f"Task exceeded {self.server.timeout_seconds} seconds and was stopped",
                },
            )
        except Exception as exc:
            self._send_json(500, {"ok": False, "error": f"Bridge error: {exc}"})
        finally:
            RUN_LOCK.release()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local Dinesh OS to Gcode bridge")
    parser.add_argument("--gcode-exe", required=True)
    parser.add_argument("--allowed-root", action="append", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8855)
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("Refusing non-local bind. Use 127.0.0.1 only.")

    gcode_exe = Path(args.gcode_exe).expanduser().resolve()
    if not gcode_exe.is_file():
        raise SystemExit(f"gcode.exe not found: {gcode_exe}")

    allowed_roots: list[Path] = []
    for raw in args.allowed_root:
        root = Path(raw).expanduser().resolve()
        if not root.is_dir():
            raise SystemExit(f"Allowed root does not exist: {root}")
        allowed_roots.append(root)

    server = BridgeServer(
        ("127.0.0.1", args.port),
        Handler,
        gcode_exe=gcode_exe,
        allowed_roots=allowed_roots,
        timeout_seconds=max(30, args.timeout),
    )
    print(f"[dinesh-gcode-bridge] READY http://127.0.0.1:{args.port}")
    print("[dinesh-gcode-bridge] FREE_ONLY=openrouter/free | telemetry=off | local-only")
    print("[dinesh-gcode-bridge] Allowed roots:")
    for root in allowed_roots:
        print(f"  - {root}")
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
