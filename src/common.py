"""Shared helpers: paths, errors, ffprobe/ffmpeg wrappers, output-path handling."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_VIDEO = PROJECT_ROOT / "input" / "movie.mp4"
DEFAULT_SOURCE_FACE = PROJECT_ROOT / "input" / "person.jpg"
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "final_movie.mp4"
MODELS_DIR = PROJECT_ROOT / "models"
DEFAULT_SWAPPER = MODELS_DIR / "inswapper_128.onnx"
DEFAULT_DETECTOR = MODELS_DIR / "buffalo_l" / "det_10g.onnx"
DEFAULT_RECOGNIZER = MODELS_DIR / "buffalo_l" / "w600k_r50.onnx"

CPU_PROVIDERS = ["CPUExecutionProvider"]


class ToolError(Exception):
    """A user-facing error: the message should say what to do next."""


def show(path: Path | str) -> str:
    """Path relative to the current directory when possible."""
    p = Path(path)
    try:
        return str(p.resolve().relative_to(Path.cwd().resolve()))
    except ValueError:
        return str(p)


def fmt_duration(seconds: float) -> str:
    seconds = int(round(max(seconds, 0)))
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}h{m:02d}m{s:02d}s"
    if m:
        return f"{m}m{s:02d}s"
    return f"{s}s"


def require_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(
            f"'{name}' was not found on PATH. Install FFmpeg, e.g. "
            "'sudo apt install ffmpeg' (Debian/Ubuntu/Mint), "
            "'sudo dnf install ffmpeg' (Fedora, needs RPM Fusion), "
            "'sudo pacman -S ffmpeg' (Arch)."
        )
    return path


@dataclass
class VideoInfo:
    path: Path
    width: int
    height: int
    fps: float
    duration: float
    nb_frames: int
    has_audio: bool
    vfr_suspected: bool


def _parse_rate(value: str | None) -> float:
    if not value or "/" not in value:
        try:
            return float(value) if value else 0.0
        except ValueError:
            return 0.0
    num, den = value.split("/", 1)
    try:
        return float(num) / float(den) if float(den) else 0.0
    except ValueError:
        return 0.0


def _to_float(value) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def probe_video(path: Path) -> VideoInfo:
    """Inspect a video with ffprobe and validate dimensions and frame rate."""
    require_tool("ffprobe")
    if not path.exists():
        raise ToolError(f"Video not found: {show(path)}")
    if not path.is_file():
        raise ToolError(f"Video path is not a file: {show(path)}")
    if not os.access(path, os.R_OK):
        raise ToolError(f"Video is not readable (permissions): {show(path)}")

    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json",
         "-show_streams", "-show_format", str(path)],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ToolError(
            f"ffprobe could not read {show(path)}; the file may be corrupt or not a video.\n"
            f"ffprobe said: {proc.stderr.strip()[-400:]}"
        )
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        raise ToolError(f"ffprobe returned unreadable output for {show(path)}.")

    streams = data.get("streams", [])
    # Skip cover-art streams; they are "video" streams with a single frame.
    video = next(
        (s for s in streams
         if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")),
        None,
    )
    if video is None:
        raise ToolError(f"No video stream found in {show(path)}.")

    width = int(video.get("width") or 0)
    height = int(video.get("height") or 0)
    if width <= 0 or height <= 0:
        raise ToolError(f"Invalid video dimensions ({width}x{height}) in {show(path)}.")

    avg = _parse_rate(video.get("avg_frame_rate"))
    raw = _parse_rate(video.get("r_frame_rate"))
    fps = avg if avg > 0 else raw
    if not (0 < fps <= 240):
        raise ToolError(
            f"Invalid frame rate ({fps}) in {show(path)}. "
            "Re-encode it first, e.g. ffmpeg -i in.mp4 -r 30 fixed.mp4"
        )
    vfr = avg > 0 and raw > 0 and abs(avg - raw) / raw > 0.02

    duration = _to_float(video.get("duration")) or _to_float(data.get("format", {}).get("duration"))
    nb_frames = int(_to_float(video.get("nb_frames")))
    if nb_frames <= 0 and duration > 0:
        nb_frames = int(round(duration * fps))
    if duration <= 0 and nb_frames > 0:
        duration = nb_frames / fps
    if nb_frames <= 0:
        raise ToolError(f"Could not determine the length of {show(path)}.")

    has_audio = any(s.get("codec_type") == "audio" for s in streams)
    return VideoInfo(path, width, height, fps, duration, nb_frames, has_audio, vfr)


def run_ffmpeg(args: list[str], what: str) -> None:
    """Run ffmpeg quietly; raise ToolError with the stderr tail on failure."""
    require_tool("ffmpeg")
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-loglevel", "error", *args],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        raise ToolError(f"ffmpeg failed while {what}:\n{proc.stderr.strip()[-800:]}")


def resolve_output_path(path: Path, overwrite: bool, protect: list[Path] | None = None) -> Path:
    """Never silently overwrite: require --overwrite or ask for another filename."""
    path = path.expanduser()
    protected = {p.resolve() for p in (protect or [])}
    while True:
        if path.resolve() in protected:
            problem = f"'{show(path)}' is an input file."
        elif path.is_dir():
            problem = f"'{show(path)}' is a directory."
        elif path.exists() and not overwrite:
            problem = f"'{show(path)}' already exists."
        else:
            return path
        if not sys.stdin.isatty():
            raise ToolError(
                f"{problem}\nChoose a different --output (or pass --overwrite to replace an existing output file)."
            )
        answer = input(f"{problem} Enter a different filename (blank to abort): ").strip()
        if not answer:
            raise ToolError("Aborted: no usable output filename.")
        candidate = Path(answer).expanduser()
        path = path.parent / candidate if candidate.parent == Path(".") else candidate


def check_free_space(directory: Path, needed_bytes: int) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    free = shutil.disk_usage(directory).free
    if free < needed_bytes:
        raise ToolError(
            f"Not enough free disk space in {show(directory)}: "
            f"{free / 1e9:.1f} GB free, about {needed_bytes / 1e9:.1f} GB needed. "
            "Free some space, use --max-seconds, or choose --output on another disk."
        )
