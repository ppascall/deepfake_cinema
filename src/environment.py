"""Environment / dry-run checks shared by scripts/check_environment.py and --dry-run."""
from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from .common import (DEFAULT_DETECTOR, DEFAULT_RECOGNIZER, DEFAULT_SWAPPER, CPU_PROVIDERS,
                     ToolError, probe_video, show)

OK, WARN, FAIL = "OK", "WARN", "FAIL"


@dataclass
class Check:
    status: str
    name: str
    detail: str = ""
    hint: str = ""


def _check_python() -> Check:
    v = sys.version_info
    if v < (3, 10):
        return Check(FAIL, "Python version", f"{v.major}.{v.minor}.{v.micro}",
                     "Python 3.10+ is required. Install a newer python3 and recreate .venv.")
    return Check(OK, "Python version", f"{v.major}.{v.minor}.{v.micro}")


def _check_import(module: str, pip_name: str | None = None) -> Check:
    try:
        mod = importlib.import_module(module)
    except Exception as exc:
        return Check(FAIL, f"Python package: {module}", f"import failed ({exc})",
                     f"Activate .venv and run: python -m pip install -r requirements.txt"
                     + (f" (package '{pip_name}')" if pip_name else ""))
    return Check(OK, f"Python package: {module}", getattr(mod, "__version__", ""))


def _check_binary(name: str) -> Check:
    path = shutil.which(name)
    if not path:
        return Check(FAIL, f"{name} on PATH", "not found",
                     "Install FFmpeg: sudo apt install ffmpeg (Debian/Ubuntu/Mint), "
                     "sudo dnf install ffmpeg (Fedora + RPM Fusion), sudo pacman -S ffmpeg (Arch).")
    try:
        first = subprocess.run([name, "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    except Exception:
        first = path
    return Check(OK, f"{name} on PATH", first)


def _check_ort_cpu() -> Check:
    try:
        import onnxruntime as ort
    except Exception:
        return Check(FAIL, "ONNX Runtime CPU provider", "onnxruntime not importable",
                     "python -m pip install onnxruntime")
    providers = ort.get_available_providers()
    if "CPUExecutionProvider" not in providers:
        return Check(FAIL, "ONNX Runtime CPU provider", f"available: {providers}",
                     "Reinstall the CPU build: python -m pip install --force-reinstall onnxruntime")
    extra = [p for p in providers if p != "CPUExecutionProvider"]
    note = f"CPUExecutionProvider (other providers present but not used: {extra})" if extra else "CPUExecutionProvider"
    return Check(OK, "ONNX Runtime CPU provider", note)


def _check_machine() -> list[Check]:
    model = "unknown CPU"
    try:
        for line in Path("/proc/cpuinfo").read_text().splitlines():
            if line.startswith("model name"):
                model = line.split(":", 1)[1].strip()
                break
    except OSError:
        pass
    checks = [Check(OK, "CPU", f"{model}, {os.cpu_count()} logical cores")]
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable"):
                gb = int(line.split()[1]) / 1e6
                if gb < 4:
                    checks.append(Check(WARN, "Available RAM", f"{gb:.1f} GB",
                                        "Low memory. Close other applications or use a smaller --scale-height."))
                else:
                    checks.append(Check(OK, "Available RAM", f"{gb:.1f} GB"))
                break
    except OSError:
        pass
    return checks


def _check_model_file(path: Path, label: str) -> Check:
    if not path.exists():
        return Check(FAIL, f"Model file: {label}", f"missing: {show(path)}",
                     "Obtain authorized weights and place them at this exact path "
                     "(README.md, 'Models and licensing'). Nothing is downloaded automatically.")
    if not path.is_file() or path.stat().st_size < 1024:
        return Check(FAIL, f"Model file: {label}", f"invalid or truncated: {show(path)}",
                     "Re-copy the model file; it looks incomplete.")
    return Check(OK, f"Model file: {label}", f"{show(path)} ({path.stat().st_size / 1e6:.0f} MB)")


def _check_model_loads(swapper: Path) -> Check:
    """Open the swapper with ONNX Runtime and verify its input signature."""
    try:
        import onnxruntime as ort
        sess = ort.InferenceSession(str(swapper), providers=CPU_PROVIDERS)
        ins = sess.get_inputs()
        shapes = [i.shape for i in ins]
        if len(ins) != 2 or shapes[0][1:] != [3, 128, 128] or shapes[1][-1] != 512:
            return Check(FAIL, "Swapper model signature", f"inputs: {shapes}",
                         "Expected an InSwapper-style model: image [1,3,128,128] + embedding [1,512].")
        return Check(OK, "Swapper model signature", f"inputs {shapes}, CPU session created")
    except Exception as exc:
        return Check(FAIL, "Swapper model signature", f"could not open model: {exc}",
                     "The file may be corrupt or not a compatible ONNX model.")


def _check_output_dir(output_dir: Path) -> list[Check]:
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
        probe = output_dir / ".write_test"
        probe.write_text("x")
        probe.unlink()
    except OSError as exc:
        return [Check(FAIL, "Output directory writable", f"{show(output_dir)}: {exc}",
                      "Choose a writable directory with --output.")]
    free = shutil.disk_usage(output_dir).free / 1e9
    return [Check(OK, "Output directory writable", show(output_dir)),
            Check(OK if free >= 2 else WARN, "Free disk space", f"{free:.1f} GB",
                  "" if free >= 2 else "Very little space; long runs need several GB.")]


def run_checks(video: Path | None = None, source_face: Path | None = None,
               swapper: Path = DEFAULT_SWAPPER, detector: Path = DEFAULT_DETECTOR,
               recognizer: Path = DEFAULT_RECOGNIZER, output_dir: Path | None = None) -> list[Check]:
    checks = [_check_python()]
    checks += [_check_import("numpy"), _check_import("cv2", "opencv-python"),
               _check_import("onnxruntime"), _check_import("insightface")]
    checks += [_check_binary("ffmpeg"), _check_binary("ffprobe"), _check_ort_cpu()]
    checks += _check_machine()

    models = [(swapper, "swapper"), (detector, "face detector"), (recognizer, "face recognizer")]
    model_checks = [_check_model_file(p, label) for p, label in models]
    checks += model_checks
    if model_checks[0].status == OK:
        checks.append(_check_model_loads(swapper))

    if output_dir is not None:
        checks += _check_output_dir(output_dir)

    if video is not None:
        try:
            info = probe_video(video)
            audio = "with audio" if info.has_audio else "no audio track"
            checks.append(Check(OK, "Input video", f"{show(video)}: {info.width}x{info.height} @ {info.fps:.2f} fps, "
                                                   f"{info.duration:.1f}s, ~{info.nb_frames} frames, {audio}"))
            if info.vfr_suspected:
                checks.append(Check(WARN, "Video frame rate", "variable frame rate suspected",
                                    "Audio sync may drift. Convert first: ffmpeg -i in.mp4 -vsync cfr -r 30 out.mp4"))
        except ToolError as exc:
            checks.append(Check(FAIL, "Input video", str(exc), "Put the video at input/movie.mp4 or pass --video."))

    if source_face is not None:
        checks.append(_check_source_face(source_face, detector, recognizer,
                                         all(c.status == OK for c in model_checks[1:])))
    return checks


def _check_source_face(photo: Path, detector: Path, recognizer: Path, models_ok: bool) -> Check:
    if not photo.exists():
        return Check(FAIL, "Source face photo", f"not found: {show(photo)}",
                     "Put the photo at input/person.jpg or pass --source-face.")
    try:
        import cv2
        if cv2.imread(str(photo)) is None:
            return Check(FAIL, "Source face photo", f"unreadable image: {show(photo)}", "Use a JPG or PNG file.")
    except ImportError:
        return Check(WARN, "Source face photo", f"{show(photo)} exists (cannot decode: OpenCV missing)")
    if not models_ok:
        return Check(WARN, "Source face photo", f"{show(photo)} readable; face detection skipped (models missing)")
    try:
        from .detect_faces import FaceAnalyzer
        faces = FaceAnalyzer(detector, recognizer).detect(cv2.imread(str(photo)))
    except ToolError as exc:
        return Check(FAIL, "Source face photo", str(exc))
    if not faces:
        return Check(FAIL, "Source face photo", f"no face detected in {show(photo)}",
                     "Use a clear, front-facing, evenly lit, sharp photo.")
    if len(faces) > 1:
        return Check(WARN, "Source face photo", f"{len(faces)} faces found; the largest will be used",
                     "Crop the photo to a single face for best results.")
    return Check(OK, "Source face photo", f"{show(photo)}: 1 face detected")


def print_report(checks: list[Check]) -> bool:
    """Print results; return True when no check failed."""
    width = max(len(c.name) for c in checks)
    for c in checks:
        print(f"[{c.status:<4}] {c.name:<{width}}  {c.detail}")
        if c.status != OK and c.hint:
            print(f"       -> {c.hint}")
    failed = [c for c in checks if c.status == FAIL]
    warned = [c for c in checks if c.status == WARN]
    print()
    if failed:
        print(f"{len(failed)} required check(s) failed. Fix them before processing a movie.")
        return False
    print("All required checks passed." + (f" ({len(warned)} warning(s))" if warned else ""))
    return True
