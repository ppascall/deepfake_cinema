"""Run one video-processing task on a temporary Runcrate GPU instance."""
from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path, PurePosixPath

from .common import PROJECT_ROOT, ToolError, show

STARTUP_TIMEOUT_SECONDS = 20 * 60
POLL_INTERVAL_SECONDS = 10
REMOTE_USER = "root"


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Process a video on a temporary Runcrate GPU instance.")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--source-face", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--gpu-type", default=os.environ.get("RUNCRATE_GPU_TYPE", "RTX4090"))
    ap.add_argument("--ssh-key-id", default=os.environ.get("RUNCRATE_SSH_KEY_ID", ""))
    ssh_key_path = os.environ.get("RUNCRATE_SSH_PRIVATE_KEY_PATH")
    ap.add_argument("--ssh-private-key", type=Path,
                    default=Path(ssh_key_path) if ssh_key_path else None)
    ap.add_argument("--max-runtime-minutes", type=int,
                    default=int(os.environ.get("RUNCRATE_MAX_RUNTIME_MINUTES", "240")))
    return ap


def validate_args(args) -> tuple[str, Path]:
    api_key = os.environ.get("RUNCRATE_API_KEY", "").strip()
    if not api_key:
        raise ToolError("RUNCRATE_API_KEY is empty. Add your Runcrate API key to the local .env file.")
    if not args.ssh_key_id.strip():
        raise ToolError("RUNCRATE_SSH_KEY_ID is required. Add the ID of your registered Runcrate SSH key to .env.")
    if args.ssh_private_key is None:
        raise ToolError("RUNCRATE_SSH_PRIVATE_KEY_PATH is required. Configure the local private-key path in .env.")
    private_key = args.ssh_private_key.expanduser().resolve()
    if not private_key.is_file():
        raise ToolError(f"SSH private key not found: {show(private_key)}")
    if not args.gpu_type.strip():
        raise ToolError("RUNCRATE_GPU_TYPE must not be empty.")
    if not 5 <= args.max_runtime_minutes <= 10080:
        raise ToolError("RUNCRATE_MAX_RUNTIME_MINUTES must be between 5 and 10080.")
    if not args.video.is_file():
        raise ToolError(f"Input video not found: {show(args.video)}")
    if not args.source_face.is_file():
        raise ToolError(f"Source face photo not found: {show(args.source_face)}")
    return api_key, private_key


def stage_project(stage_root: Path, video: Path, source_face: Path,
                  project_root: Path = PROJECT_ROOT) -> tuple[Path, Path, Path]:
    upload_dir = stage_root / f"deepfake_cinema_{uuid.uuid4().hex[:8]}"
    upload_dir.mkdir(parents=True)
    shutil.copytree(project_root / "src", upload_dir / "src",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    for requirement_file in ("requirements.txt", "requirements-gpu.txt"):
        source = project_root / requirement_file
        if not source.is_file():
            raise ToolError(f"Required remote dependency file not found: {show(source)}")
        shutil.copy2(source, upload_dir / requirement_file)

    models = upload_dir / "models"
    (models / "buffalo_l").mkdir(parents=True)
    model_files = (
        (project_root / "models" / "inswapper_128.onnx", models / "inswapper_128.onnx"),
        (project_root / "models" / "buffalo_l" / "det_10g.onnx", models / "buffalo_l" / "det_10g.onnx"),
        (project_root / "models" / "buffalo_l" / "w600k_r50.onnx", models / "buffalo_l" / "w600k_r50.onnx"),
    )
    for source, destination in model_files:
        if not source.is_file():
            raise ToolError(f"Required model file not found: {show(source)}")
        shutil.copy2(source, destination)

    input_dir = upload_dir / "input"
    input_dir.mkdir()
    remote_video = input_dir / f"video{video.suffix.lower() or '.mp4'}"
    remote_face = input_dir / f"source_face{source_face.suffix.lower() or '.jpg'}"
    shutil.copy2(video, remote_video)
    shutil.copy2(source_face, remote_face)
    return upload_dir, remote_video.relative_to(upload_dir), remote_face.relative_to(upload_dir)


def remote_process_command(video: Path, source_face: Path, output: Path,
                           max_seconds: float | None) -> str:
    command = [
        "set -eu",
        "export DEBIAN_FRONTEND=noninteractive",
        "apt-get update",
        "apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 libgomp1 build-essential python3-dev python3-venv python3-pip",
        "python3 -m venv .venv",
        ".venv/bin/python -m pip install --no-cache-dir -r requirements.txt",
        ".venv/bin/python -m pip uninstall -y onnxruntime",
        ".venv/bin/python -m pip install --no-cache-dir -r requirements-gpu.txt",
    ]
    processor = [
        ".venv/bin/python", "-m", "src.process_video",
        "--video", str(video),
        "--source-face", str(source_face),
        "--model", "models/inswapper_128.onnx",
        "--det-model", "models/buffalo_l/det_10g.onnx",
        "--rec-model", "models/buffalo_l/w600k_r50.onnx",
        "--device", "cuda",
        "--output", str(output),
        "--overwrite",
    ]
    if max_seconds is not None:
        processor.extend(("--max-seconds", str(max_seconds)))
    command.append(shlex.join(processor))
    return " && ".join(command)


def ssh_options(private_key: Path, known_hosts: Path, port: int) -> list[str]:
    return [
        "-p", str(port),
        "-i", str(private_key),
        "-o", "BatchMode=yes",
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", f"UserKnownHostsFile={known_hosts}",
        "-o", "ConnectTimeout=20",
        "-o", "ServerAliveInterval=30",
    ]


def scp_options(private_key: Path, known_hosts: Path, port: int) -> list[str]:
    options = ssh_options(private_key, known_hosts, port)
    options[0] = "-P"
    return options


def run_ssh(command: list[str]) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
    except OSError as exc:
        raise ToolError(f"Could not start SSH. Install OpenSSH client in the app environment: {exc}") from exc
    if process.stdout is not None:
        for line in process.stdout:
            print(line, end="", flush=True)
    if process.wait() != 0:
        raise ToolError("Remote SSH command failed. Review the remote output above.")


def run_scp(command: list[str]) -> None:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ToolError(f"Could not start SCP. Install OpenSSH client in the app environment: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ToolError(f"SCP transfer failed: {detail[-1200:]}")


def wait_for_instance(client, instance_id: str):
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        status = client.instances.get_status(instance_id)
        state = str(getattr(status, "status", "")).lower()
        print(f"Runcrate instance {instance_id}: {state or 'waiting'}", flush=True)
        if state in {"running", "deployed"}:
            host = getattr(status, "ssh_host", None) or getattr(status, "ip", None)
            if host:
                return status
        if state in {"error", "terminated", "failed"}:
            raise ToolError(f"Runcrate instance entered state '{state}' before SSH was ready.")
        time.sleep(POLL_INTERVAL_SECONDS)
    raise ToolError(f"Timed out waiting for Runcrate instance {instance_id} to become SSH-ready.")


def process(args) -> Path:
    api_key, source_key = validate_args(args)
    try:
        from runcrate import Runcrate
    except ImportError as exc:
        raise ToolError("Runcrate SDK is missing. Rebuild the app after installing requirements.txt.") from exc

    args.output.parent.mkdir(parents=True, exist_ok=True)
    instance = None
    with tempfile.TemporaryDirectory(prefix=".runcrate_", dir=args.output.parent) as temp_dir:
        temp_root = Path(temp_dir)
        upload_dir, remote_video, remote_face = stage_project(temp_root, args.video, args.source_face)
        private_key = temp_root / "ssh_key"
        shutil.copyfile(source_key, private_key)
        private_key.chmod(0o600)
        known_hosts = temp_root / "known_hosts"
        known_hosts.touch()

        with Runcrate(api_key=api_key) as client:
            try:
                name = f"deepfake-cinema-{uuid.uuid4().hex[:8]}"
                print(f"Requesting one {args.gpu_type} GPU instance...", flush=True)
                instance = client.instances.create(
                    name=name,
                    ssh_key_id=args.ssh_key_id,
                    gpu_type=args.gpu_type,
                    gpu_count=1,
                    max_runtime_minutes=args.max_runtime_minutes,
                )
                print(f"Created Runcrate instance {instance.id}.", flush=True)
                status = wait_for_instance(client, instance.id)
                host = getattr(status, "ssh_host", None) or status.ip
                port = int(getattr(status, "ssh_port", None) or 22)
                remote_dir = f"/root/{upload_dir.name}"
                destination = f"{REMOTE_USER}@{host}"
                ssh = ["ssh", *ssh_options(private_key, known_hosts, port), destination]
                run_scp(["scp", "-r", *scp_options(private_key, known_hosts, port), str(upload_dir),
                         f"{destination}:/root/"])
                remote_output = f"{remote_dir}/output.mp4"
                command = remote_process_command(
                    PurePosixPath(remote_dir) / PurePosixPath(remote_video.as_posix()),
                    PurePosixPath(remote_dir) / PurePosixPath(remote_face.as_posix()),
                    PurePosixPath(remote_output),
                    args.max_seconds,
                )
                run_ssh(ssh + [f"cd {shlex.quote(remote_dir)} && {command}"])
                run_scp(["scp", *scp_options(private_key, known_hosts, port),
                         f"{destination}:{remote_output}", str(args.output)])
                if not args.output.is_file() or args.output.stat().st_size == 0:
                    raise ToolError("Remote processing completed but no output video was downloaded.")
                print(f"Downloaded remote result to {show(args.output)}.", flush=True)
                return args.output
            finally:
                if instance is not None:
                    try:
                        print(f"Terminating Runcrate instance {instance.id}...", flush=True)
                        client.instances.terminate(instance.id)
                        print("Runcrate instance terminated.", flush=True)
                    except Exception as exc:
                        print(f"WARNING: Could not terminate Runcrate instance {instance.id}: {exc}",
                              file=sys.stderr, flush=True)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        process(args)
        return 0
    except ToolError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nRemote processing interrupted. The instance is being terminated.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nERROR: Runcrate processing failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())