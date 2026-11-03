#!/usr/bin/env python3
"""Narrow, token-authenticated bridge from the Docker UI to the host Runcrate CLI."""
from __future__ import annotations

import argparse
import hmac
import json
import math
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MAX_REQUEST_BYTES = 16 * 1024
MAX_LOG_LINES = 500
MAX_LOG_LINE_LENGTH = 2000
TOKEN_PATTERN = re.compile(
    r"(?:\brc_[A-Za-z0-9._-]+|Bearer\s+\S+|"
    r"\b(?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret)\s*[:=]\s*\S+|"
    r"-----BEGIN [^-]+PRIVATE KEY-----)",
    re.IGNORECASE,
)


def sanitize_log(text: str) -> str:
    return TOKEN_PATTERN.sub("[credential redacted]", text.rstrip())[:MAX_LOG_LINE_LENGTH]


class JobManager:
    def __init__(self, project_root: Path, python: str):
        self.project_root = project_root.resolve()
        self.python = python
        self.lock = threading.Lock()
        self.job: dict | None = None

    def _resolve_path(self, relative: object, allowed_roots: tuple[str, ...], *, output: bool = False) -> Path:
        if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
            raise ValueError("A valid project-relative file path is required.")
        candidate = (self.project_root / relative).resolve()
        if not candidate.is_relative_to(self.project_root):
            raise ValueError("The requested file path is outside the project.")
        if not any(candidate.is_relative_to(self.project_root / root) for root in allowed_roots):
            raise ValueError("The requested file path is outside the permitted project folders.")
        if output:
            if candidate.suffix.lower() != ".mp4":
                raise ValueError("The output path must be an MP4 file.")
            if not candidate.parent.is_dir():
                raise ValueError("The output directory does not exist.")
        elif not candidate.is_file():
            raise ValueError(f"Selected input file does not exist: {relative}")
        return candidate

    def submit(self, request_data: object) -> dict:
        if not isinstance(request_data, dict):
            raise ValueError("The job request must be a JSON object.")

        instance = request_data.get("instance", "face-swap")
        if not isinstance(instance, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", instance):
            raise ValueError("The configured Runcrate instance name is invalid.")
        video = self._resolve_path(request_data.get("video"), ("input", "output/clips"))
        source_face = self._resolve_path(
            request_data.get("sourceFace"), ("input", "output/captured_faces")
        )
        output = self._resolve_path(request_data.get("output"), ("output",), output=True)
        max_seconds = request_data.get("maxSeconds")
        if max_seconds is not None and (
            isinstance(max_seconds, bool) or not isinstance(max_seconds, (int, float))
            or not math.isfinite(max_seconds) or max_seconds < 0
        ):
            raise ValueError("maxSeconds must be a non-negative number.")
        shutdown_after_job = request_data.get("shutdownAfterJob", False)
        if not isinstance(shutdown_after_job, bool):
            raise ValueError("shutdownAfterJob must be true or false.")

        with self.lock:
            if self.job and self.job["state"] == "running":
                raise RuntimeError("A Runcrate job is already running.")
            job_id = os.urandom(16).hex()
            self.job = {
                "id": job_id,
                "state": "running",
                "exitCode": None,
                "logs": ["Handing the selected scene to the authenticated Runcrate CLI..."],
            }
            args = [
                self.python, "-u", "-m", "src.runcrate_worker",
                "--instance", instance,
                "--video", str(video),
                "--source-face", str(source_face),
                "--output", str(output),
            ]
            if max_seconds is not None:
                args.extend(("--max-seconds", str(max_seconds)))
            if shutdown_after_job:
                args.append("--shutdown-after-job")
            worker_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", ""),
                "LANG": os.environ.get("LANG", "C.UTF-8"),
                "PYTHONUNBUFFERED": "1",
                "RUNCRATE_INSTANCE": instance,
            }
            for name in ("XDG_CONFIG_HOME", "XDG_STATE_HOME", "TMPDIR"):
                if os.environ.get(name):
                    worker_env[name] = os.environ[name]
            try:
                process = subprocess.Popen(
                    args,
                    cwd=self.project_root,
                    env=worker_env,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                )
            except OSError as exc:
                self.job.update(state="failed", exitCode=1)
                self.job["logs"].append(sanitize_log(f"Could not start the Runcrate worker: {exc}"))
                raise RuntimeError("Could not start the Runcrate worker.") from exc

        threading.Thread(target=self._collect, args=(process, job_id), daemon=True).start()
        return {"jobId": job_id, "state": "running"}

    def _collect(self, process: subprocess.Popen, job_id: str) -> None:
        if process.stdout is not None:
            for raw_line in process.stdout:
                line = sanitize_log(raw_line)
                with self.lock:
                    if self.job and self.job["id"] == job_id and line:
                        self.job["logs"].append(line)
                        if len(self.job["logs"]) > MAX_LOG_LINES:
                            del self.job["logs"][:-MAX_LOG_LINES]
        exit_code = process.wait()
        with self.lock:
            if self.job and self.job["id"] == job_id:
                self.job["exitCode"] = exit_code
                self.job["state"] = "succeeded" if exit_code == 0 else "failed"

    def snapshot(self, job_id: str) -> dict | None:
        with self.lock:
            if not self.job or self.job["id"] != job_id:
                return None
            return dict(self.job, logs=list(self.job["logs"]))


def create_server(token: str, *, host: str, port: int, project_root: Path = PROJECT_ROOT,
                  python: str = sys.executable) -> ThreadingHTTPServer:
    manager = JobManager(project_root, python)

    class Handler(BaseHTTPRequestHandler):
        server_version = "DeepfakeCinemaRuncrateBridge/1"

        def log_message(self, _format: str, *_args) -> None:
            return

        def _send_json(self, status_code: int, data: dict) -> None:
            payload = json.dumps(data).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def _authorized(self) -> bool:
            supplied = self.headers.get("Authorization", "")
            expected = f"Bearer {token}"
            if not hmac.compare_digest(supplied, f"Bearer {self.server.bridge_token}"):
                self._send_json(401, {"error": "Runcrate bridge authorization failed."})
                return False
            return True

        def do_POST(self) -> None:
            if urlparse(self.path).path != "/jobs":
                self._send_json(404, {"error": "Bridge endpoint not found."})
                return
            if not self._authorized():
                return
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
                if content_length < 1 or content_length > MAX_REQUEST_BYTES:
                    self._send_json(413, {"error": "The job request is empty or too large."})
                    return
                request_data = json.loads(self.rfile.read(content_length))
                result = manager.submit(request_data)
                self._send_json(202, result)
            except (ValueError, json.JSONDecodeError) as exc:
                self._send_json(400, {"error": str(exc)})
            except RuntimeError as exc:
                self._send_json(409, {"error": str(exc)})

        def do_GET(self) -> None:
            if not self._authorized():
                return
            pieces = urlparse(self.path).path.strip("/").split("/")
            if len(pieces) != 2 or pieces[0] != "jobs":
                self._send_json(404, {"error": "Bridge endpoint not found."})
                return
            snapshot = manager.snapshot(pieces[1])
            if snapshot is None:
                self._send_json(404, {"error": "Runcrate job not found."})
            else:
                self._send_json(200, snapshot)

    server = ThreadingHTTPServer((host, port), Handler)
    server.bridge_token = token
    return server


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True, help="Docker bridge gateway interface address.")
    parser.add_argument("--port", type=int, default=38765)
    parser.add_argument("--token-file", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        token = args.token_file.read_text(encoding="utf-8").strip()
    except OSError as exc:
        print(f"ERROR: Cannot read the local bridge token file: {exc}", file=sys.stderr)
        return 2
    if len(token) < 32:
        print("ERROR: The local bridge token must contain at least 32 characters.", file=sys.stderr)
        return 2

    try:
        server = create_server(token, host=args.host, port=args.port)
    except OSError as exc:
        print(f"ERROR: Could not start the Runcrate bridge on the Docker gateway: {exc}", file=sys.stderr)
        return 2
    print("Authenticated Runcrate CLI bridge is ready for the local web app.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
