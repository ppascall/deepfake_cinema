"""Process one selected video segment on an existing Runcrate GPU instance."""
from __future__ import annotations

import argparse
import json
import os
import re
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
DEFAULT_INSTANCE = "face-swap"
REMOTE_ROOT = PurePosixPath("/root")
MODEL_PATHS = (
    (Path("models/inswapper_128.onnx"), "face swapper"),
    (Path("models/buffalo_l/det_10g.onnx"), "face detector"),
    (Path("models/buffalo_l/w600k_r50.onnx"), "face recognizer"),
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Process one selected video segment on an existing Runcrate GPU.")
    ap.add_argument("--video", type=Path, required=True)
    ap.add_argument("--source-face", type=Path, required=True)
    ap.add_argument("--output", type=Path, required=True)
    ap.add_argument("--max-seconds", type=float)
    ap.add_argument("--instance", default=os.environ.get("RUNCRATE_INSTANCE", DEFAULT_INSTANCE))
    ap.add_argument("--shutdown-after-job", action="store_true",
                    help="Delete the existing GPU instance after a successful download.")
    return ap


def validate_args(args) -> None:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", args.instance):
        raise ToolError("RUNCRATE_INSTANCE must be a valid instance name or ID.")
    if not args.video.is_file():
        raise ToolError(f"Selected video segment not found: {show(args.video)}")
    if not args.source_face.is_file():
        raise ToolError(f"Selected source face not found: {show(args.source_face)}")
    if args.output.suffix.lower() != ".mp4":
        raise ToolError("Output path must end in .mp4.")
    if args.max_seconds is not None and args.max_seconds < 0:
        raise ToolError("--max-seconds must be greater than 0 (or 0 for the whole clip).")
    for model_path, label in MODEL_PATHS:
        full_path = PROJECT_ROOT / model_path
        if not full_path.is_file() or full_path.stat().st_size < 1024:
            raise ToolError(
                f"Required {label} model is missing or invalid: {show(full_path)}. "
                "Place authorized model files at the documented paths."
            )
    if not shutil.which("runcrate"):
        raise ToolError("Runcrate CLI was not found on PATH. Install it and run `runcrate login` on this laptop.")


def run_cli(command: list[str], action: str) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
    except OSError as exc:
        raise ToolError(f"Could not run the Runcrate CLI while {action}: {exc}") from exc
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        if detail:
            raise ToolError(f"Runcrate CLI failed while {action}: {detail[-1200:]}")
        raise ToolError(f"Runcrate CLI failed while {action} (exit code {result.returncode}).")
    return result


def instance_status(instance: str) -> dict:
    result = run_cli(["runcrate", "instances", "status", instance, "--json"],
                     f"checking instance '{instance}'")
    try:
        status = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ToolError("Runcrate returned an invalid instance status response.") from exc
    if not isinstance(status, dict):
        raise ToolError("Runcrate returned an unexpected instance status response.")
    return status


def ensure_instance_running(instance: str) -> None:
    print(f"Checking existing Runcrate instance '{instance}'...")
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        status = instance_status(instance)
        state = str(status.get("status", "")).lower()
        print(f"Runcrate instance {instance}: {state or 'unknown'}")
        if state in {"running", "deployed"}:
            return
        if state in {"deploying", "starting", "provisioning", "pending"}:
            if time.monotonic() >= deadline:
                raise ToolError(f"Timed out waiting for Runcrate instance '{instance}' to become ready.")
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if state in {"stopped", "stopping", "terminated", "deleted", "error", "failed"}:
            raise ToolError(
                f"Runcrate instance '{instance}' is {state}. Start or recreate it in Runcrate before retrying; "
                "this app does not create replacement instances."
            )
        raise ToolError(
            f"Runcrate instance '{instance}' has unexpected status '{state or 'unknown'}'; "
            "check it with `runcrate instances status " + instance + "`."
        )


def stage_project(stage_root: Path, video: Path, source_face: Path,
                  project_root: Path = PROJECT_ROOT) -> tuple[Path, PurePosixPath, PurePosixPath]:
    """Stage only the processor/runtime files and selected media/model assets."""
    upload_dir = stage_root / f"deepfake_cinema_{uuid.uuid4().hex[:10]}"
    upload_dir.mkdir(parents=True)
    source_dir = upload_dir / "src"
    source_dir.mkdir()
    for source_file in ("__init__.py", "common.py", "detect_faces.py", "process_video.py"):
        shutil.copy2(project_root / "src" / source_file, source_dir / source_file)
    for requirement_file in ("requirements.txt", "requirements-gpu.txt"):
        source = project_root / requirement_file
        if not source.is_file():
            raise ToolError(f"Required dependency file not found: {show(source)}")
        shutil.copy2(source, upload_dir / requirement_file)

    for relative_path, label in MODEL_PATHS:
        source = project_root / relative_path
        destination = upload_dir / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not source.is_file() or source.stat().st_size < 1024:
            raise ToolError(f"Required {label} model is missing or invalid: {show(source)}")
        shutil.copy2(source, destination)

    input_dir = upload_dir / "input"
    input_dir.mkdir()
    remote_video = Path("input") / f"video{video.suffix.lower() or '.mp4'}"
    remote_face = Path("input") / f"source_face{source_face.suffix.lower() or '.jpg'}"
    shutil.copy2(video, upload_dir / remote_video)
    shutil.copy2(source_face, upload_dir / remote_face)
    return upload_dir, PurePosixPath(remote_video.as_posix()), PurePosixPath(remote_face.as_posix())


def remote_process_command(remote_dir: PurePosixPath, video: PurePosixPath,
                           source_face: PurePosixPath, output: PurePosixPath,
                           max_seconds: float | None) -> str:
    venv = PurePosixPath("/root/.cache/deepfake-cinema/venv")
    command = [
        "set -eu",
        "if ! command -v ffmpeg >/dev/null 2>&1 || ! command -v python3 >/dev/null 2>&1; then "
        "apt-get update && apt-get install -y --no-install-recommends "
        "ffmpeg libgl1 libglib2.0-0 libgomp1 build-essential python3-dev python3-venv python3-pip; fi",
        f"mkdir -p {shlex.quote(str(venv.parent))}",
        f"if [ ! -x {shlex.quote(str(venv / 'bin/python'))} ]; then "
        f"python3 -m venv {shlex.quote(str(venv))}; fi",
        f"{shlex.quote(str(venv / 'bin/python'))} -m pip install --no-cache-dir -r "
        f"{shlex.quote(str(remote_dir / 'requirements.txt'))}",
        f"{shlex.quote(str(venv / 'bin/python'))} -m pip uninstall -y onnxruntime >/dev/null 2>&1 || true",
        f"{shlex.quote(str(venv / 'bin/python'))} -m pip install --no-cache-dir -r "
        f"{shlex.quote(str(remote_dir / 'requirements-gpu.txt'))}",
    ]
    processor = [
        str(venv / "bin/python"), "-m", "src.process_video",
        "--video", str(remote_dir / video),
        "--source-face", str(remote_dir / source_face),
        "--model", str(remote_dir / "models/inswapper_128.onnx"),
        "--det-model", str(remote_dir / "models/buffalo_l/det_10g.onnx"),
        "--rec-model", str(remote_dir / "models/buffalo_l/w600k_r50.onnx"),
        "--device", "cuda",
        "--output", str(remote_dir / output),
        "--overwrite",
    ]
    if max_seconds is not None:
        processor.extend(("--max-seconds", str(max_seconds)))
    command.extend((f"cd {shlex.quote(str(remote_dir))}", shlex.join(processor)))
    return " && ".join(command)


def run_ssh(instance: str, remote_command: str) -> None:
    command = ["runcrate", "ssh", instance, "--", "bash", "-lc", remote_command]
    _run_ssh_command(command)


def run_ssh_args(instance: str, *remote_args: str) -> None:
    command = ["runcrate", "ssh", instance, "--", *remote_args]
    _run_ssh_command(command)


def _run_ssh_command(command: list[str]) -> None:
    try:
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   text=True, bufsize=1)
    except OSError as exc:
        raise ToolError(f"Could not start Runcrate remote command: {exc}") from exc
    if process.stdout is not None:
        for line in process.stdout:
            print(line, end="", flush=True)
    if process.wait() != 0:
        raise ToolError("Remote Runcrate command failed. Review the progress output above.")


def process(args) -> Path:
    validate_args(args)
    args.video = args.video.expanduser().resolve()
    args.source_face = args.source_face.expanduser().resolve()
    args.output = args.output.expanduser().resolve()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    ensure_instance_running(args.instance)

    uploaded = False
    remote_dir: PurePosixPath | None = None
    job_succeeded = False
    with tempfile.TemporaryDirectory(prefix=".runcrate_", dir=args.output.parent) as temp_dir:
        stage_root = Path(temp_dir)
        upload_dir, remote_video, remote_face = stage_project(
            stage_root, args.video, args.source_face, PROJECT_ROOT
        )
        remote_dir = REMOTE_ROOT / upload_dir.name
        remote_output = remote_dir / "output.mp4"
        downloaded_output = stage_root / "downloaded.mp4"
        try:
            print(f"Uploading selected segment, face photo, processor files, and required models to '{args.instance}'...")
            run_cli(["runcrate", "cp", "-r", str(upload_dir), f"{args.instance}:/root/"],
                    "uploading this job's files")
            uploaded = True

            print("Preparing the CUDA runtime and starting video processing...")
            remote_command = remote_process_command(
                remote_dir, remote_video, remote_face, PurePosixPath("output.mp4"), args.max_seconds
            )
            run_ssh(args.instance, remote_command)

            print("Downloading the processed video...")
            run_cli(["runcrate", "cp", f"{args.instance}:{remote_output}", str(downloaded_output)],
                    "downloading the processed video")
            if not downloaded_output.is_file() or downloaded_output.stat().st_size == 0:
                raise ToolError("Runcrate completed processing but returned an empty output video.")
            os.replace(downloaded_output, args.output)
            print(f"Downloaded result to {show(args.output)}.")
            job_succeeded = True
        finally:
            if uploaded and remote_dir is not None:
                print("Removing this job's temporary files from the GPU instance...")
                try:
                    run_ssh_args(args.instance, "rm", "-rf", "--", str(remote_dir))
                except (ToolError, KeyboardInterrupt) as exc:
                    print(f"WARNING: Could not remove remote job files: {exc}", file=sys.stderr, flush=True)

    if job_succeeded and args.shutdown_after_job:
        print(f"Deleting Runcrate instance '{args.instance}' as requested...")
        run_cli(["runcrate", "instances", "delete", args.instance],
                f"deleting instance '{args.instance}'")
        print("Runcrate instance deleted.")
    elif job_succeeded:
        print(f"Leaving Runcrate instance '{args.instance}' running as requested.")
    return args.output


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        process(args)
        return 0
    except ToolError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nRemote processing interrupted. The existing GPU instance was not shut down.", file=sys.stderr)
        return 130
    except Exception as exc:
        print(f"\nERROR: Runcrate processing failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
