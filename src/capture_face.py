"""Explicit, local capture of a still face image from a camera."""
from __future__ import annotations

import argparse
import math
import re
import sys
import time
from datetime import datetime
from pathlib import Path

from .common import (DEFAULT_DETECTOR, DEFAULT_OUTPUT, DEFAULT_RECOGNIZER, DEFAULT_SWAPPER,
                     DEFAULT_VIDEO, PROJECT_ROOT, ToolError, show)
from .detect_faces import FaceDetector, import_runtime, largest_face

DEFAULT_CAPTURE_DIR = PROJECT_ROOT / "input" / "captured_faces"
WINDOW_NAME = "Consented face capture"
CONSENT_NOTICE = (
    "Consent notice: capture only with the person's knowledge and consent, "
    "and only from authorized camera feeds."
)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(
        description="Explicitly capture a consenting person's face as a local JPEG.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    source = ap.add_mutually_exclusive_group()
    source.add_argument("--camera", type=int, default=0, help="Local camera index.")
    source.add_argument("--stream-url", help="Operator-provided IP-camera/RTSP URL (never saved or displayed).")
    ap.add_argument("--output-dir", type=Path, default=DEFAULT_CAPTURE_DIR,
                    help="Local directory for explicitly saved JPEG images.")
    ap.add_argument("--prefix", default="face", help="Filename prefix (letters, numbers, underscores, hyphens).")
    ap.add_argument("--det-model", type=Path, default=DEFAULT_DETECTOR,
                    help="Authorized SCRFD detector ONNX model.")
    ap.add_argument("--det-size", type=int, default=640,
                    help="Detector input size in pixels; multiple of 32 and at least 160.")
    ap.add_argument("--capture-and-process", action="store_true",
                    help="After a visible countdown, capture a face and immediately process the default video.")
    ap.add_argument("--capture-delay", type=float, default=5.0,
                    help="Seconds to wait before automatic capture in --capture-and-process mode.")
    ap.add_argument("--video", type=Path, default=DEFAULT_VIDEO,
                    help="Input prerecorded video in --capture-and-process mode.")
    ap.add_argument("--model", type=Path, default=DEFAULT_SWAPPER,
                    help="Authorized swapper ONNX model in --capture-and-process mode.")
    ap.add_argument("--rec-model", type=Path, default=DEFAULT_RECOGNIZER,
                    help="Authorized ArcFace model in --capture-and-process mode.")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                    help="Output MP4 path in --capture-and-process mode.")
    ap.add_argument("--max-seconds", type=float, default=5.0,
                    help="Video duration to process in --capture-and-process mode (0 = whole video).")
    ap.add_argument("--fps", type=float, default=20.0,
                    help="Output frame rate in --capture-and-process mode (0 = source rate).")
    ap.add_argument("--scale-height", type=int, default=720,
                    help="Output height in --capture-and-process mode (0 = source size).")
    ap.add_argument("--overwrite", action="store_true",
                    help="Allow replacing the output MP4 in --capture-and-process mode.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Check arguments, dependencies and detector file without opening a camera.")
    return ap


def validate_args(args) -> None:
    if args.camera < 0:
        raise ToolError("--camera must be a non-negative camera index.")
    if args.stream_url is not None and not args.stream_url.strip():
        raise ToolError("--stream-url must not be empty.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", args.prefix):
        raise ToolError("--prefix must start with a letter or number and contain only letters, numbers, '_' or '-'.")
    if args.det_size < 160 or args.det_size % 32:
        raise ToolError("--det-size must be a multiple of 32 and at least 160 (e.g. 320, 480, 640).")
    if not math.isfinite(args.capture_delay) or args.capture_delay < 0:
        raise ToolError("--capture-delay must be a finite non-negative number.")
    if args.output.suffix.lower() != ".mp4":
        raise ToolError("--output must end in .mp4.")
    if args.max_seconds < 0:
        raise ToolError("--max-seconds must be greater than 0 (or 0 for the whole video).")
    if args.fps < 0:
        raise ToolError("--fps must be greater than 0 (or 0 to keep the source frame rate).")
    if args.scale_height < 0 or (args.scale_height and args.scale_height < 64):
        raise ToolError("--scale-height must be 0 or at least 64 pixels.")


def face_bounds(face, frame_shape, margin: float = 0.0) -> tuple[int, int, int, int]:
    """Return a valid, clamped face rectangle, optionally expanded by a proportional margin."""
    height, width = frame_shape[:2]
    try:
        x1, y1, x2, y2 = (float(value) for value in face.bbox[:4])
    except (AttributeError, TypeError, ValueError, IndexError):
        raise ToolError("The face detector returned invalid box coordinates.")
    if not all(math.isfinite(value) for value in (x1, y1, x2, y2)) or x2 <= x1 or y2 <= y1:
        raise ToolError("The face detector returned invalid box coordinates.")

    pad_x = (x2 - x1) * margin
    pad_y = (y2 - y1) * margin
    left = max(0, min(width, math.floor(x1 - pad_x)))
    top = max(0, min(height, math.floor(y1 - pad_y)))
    right = max(0, min(width, math.ceil(x2 + pad_x)))
    bottom = max(0, min(height, math.ceil(y2 + pad_y)))
    if right <= left or bottom <= top:
        raise ToolError("The detected face crop is empty; try a different camera view.")
    return left, top, right, bottom


def crop_face(frame, face):
    """Crop a detected face with a modest margin, clamped to the frame."""
    left, top, right, bottom = face_bounds(face, frame.shape, margin=0.25)
    crop = frame[top:bottom, left:right]
    if crop.size == 0:
        raise ToolError("The detected face crop is empty; try a different camera view.")
    return crop


def save_jpeg(crop, output_dir: Path, prefix: str, cv2, timestamp: str | None = None) -> Path:
    """Encode and exclusively create a timestamped JPEG, never replacing an existing image."""
    ok, encoded = cv2.imencode(".jpg", crop)
    if not ok or encoded.size == 0:
        raise ToolError("OpenCV could not encode the captured face as a JPEG.")
    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise ToolError(f"Could not create capture directory {show(output_dir)}: {exc}")

    stamp = timestamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    image_bytes = encoded.tobytes()
    suffix = 0
    while True:
        extra = f"_{suffix}" if suffix else ""
        path = output_dir / f"{prefix}_{stamp}{extra}.jpg"
        created = False
        try:
            with path.open("xb") as image_file:
                created = True
                image_file.write(image_bytes)
            return path
        except FileExistsError:
            suffix += 1
        except OSError as exc:
            cleanup_error = ""
            if created:
                try:
                    path.unlink()
                except OSError as cleanup_exc:
                    cleanup_error = f" The incomplete file could not be removed: {cleanup_exc}"
            raise ToolError(f"Could not save captured JPEG to {show(path)}: {exc}.{cleanup_error}")


def run_capture(args, detector, cv2, auto_capture_after: float | None = None) -> Path | None:
    source = args.stream_url if args.stream_url is not None else args.camera
    cap = None
    try:
        try:
            if hasattr(cv2, "setLogLevel"):
                cv2.setLogLevel(getattr(cv2, "LOG_LEVEL_SILENT", 0))
            cap = cv2.VideoCapture(source)
            opened = cap.isOpened()
        except Exception:
            raise ToolError(
                "OpenCV could not initialize the selected camera input. "
                "Check the camera index or stream URL; credentials are not displayed."
            )
        if not opened:
            raise ToolError(
                "OpenCV could not open the selected camera input. Check camera permissions, "
                "the camera index, or stream availability; the stream URL is not displayed."
            )

        try:
            cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
        except Exception:
            raise ToolError("Could not open the capture window. Use an OpenCV installation with GUI support.")

        capture_started = time.monotonic()
        while True:
            try:
                ok, frame = cap.read()
            except Exception:
                raise ToolError("Could not read a frame from the selected camera input.")
            if not ok or frame is None:
                raise ToolError("Could not read a frame from the selected camera input; check that it is still available.")

            faces = detector.detect(frame)
            selected = largest_face(faces)
            display = frame.copy()
            for face in faces:
                left, top, right, bottom = face_bounds(face, frame.shape)
                color = (0, 255, 0) if face is selected else (0, 165, 255)
                cv2.rectangle(display, (left, top), (right - 1, bottom - 1), color, 2)

            elapsed = time.monotonic() - capture_started
            capture_ready = auto_capture_after is not None and elapsed >= auto_capture_after
            if selected is None:
                status = "No face detected - waiting for a face"
                instruction = "Q/Esc: quit"
            elif auto_capture_after is not None:
                remaining = max(0, math.ceil(auto_capture_after - elapsed))
                status = (f"Face selected - capturing in {remaining}s"
                          if not capture_ready else "Face selected - capturing now")
                instruction = "Q/Esc: cancel"
            else:
                status = "Face detected - selected face highlighted; press S to save"
                instruction = "Q/Esc: quit"
            cv2.putText(display, status, (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.65, (255, 255, 255), 2)
            cv2.putText(display, instruction, (12, 54), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)
            try:
                cv2.imshow(WINDOW_NAME, display)
                key = cv2.waitKey(1) & 0xFF
            except Exception:
                raise ToolError("The capture window stopped responding. Check that a desktop display is available.")

            if key in (ord("q"), 27):
                return None
            if auto_capture_after is not None and capture_ready and selected is not None:
                path = save_jpeg(crop_face(frame, selected), args.output_dir, args.prefix, cv2)
                print(f"Captured face saved to {show(path)}")
                return path
            if key in (ord("s"), ord("S")) and selected is not None:
                path = save_jpeg(crop_face(frame, selected), args.output_dir, args.prefix, cv2)
                print(f"Captured face saved to {show(path)}")
    finally:
        try:
            if cap is not None:
                cap.release()
        finally:
            cv2.destroyAllWindows()


def process(args) -> int:
    validate_args(args)
    cv2, _ = import_runtime()
    print(CONSENT_NOTICE)
    if args.dry_run:
        from .detect_faces import require_model_file
        require_model_file(args.det_model, "face detector")
        print("Dry run: dependencies and detector file are available; no camera was opened and no image was saved.")
        return 0

    detector = FaceDetector(args.det_model, det_size=args.det_size)
    if args.capture_and_process:
        print(f"Automatic capture starts after {args.capture_delay:g} seconds; press Q or Escape to cancel.")
    else:
        print("Press S to save the highlighted largest face; press Q or Escape to quit.")
    if args.stream_url is not None:
        print("Capture source: IP-camera/RTSP stream (URL omitted).")
    else:
        print(f"Capture source: camera {args.camera}.")
    if not args.capture_and_process:
        run_capture(args, detector, cv2)
        return 0

    captured_path = run_capture(args, detector, cv2, auto_capture_after=args.capture_delay)
    if captured_path is None:
        print("Capture cancelled; video was not processed.")
        return 130

    print(f"Processing {show(args.video)} with the captured face...")
    from .process_video import main as process_video_main
    processor_args = [
        "--video", str(args.video),
        "--source-face", str(captured_path),
        "--model", str(args.model),
        "--det-model", str(args.det_model),
        "--rec-model", str(args.rec_model),
        "--det-size", str(args.det_size),
        "--output", str(args.output),
        "--max-seconds", str(args.max_seconds),
        "--fps", str(args.fps),
        "--scale-height", str(args.scale_height),
    ]
    if args.overwrite:
        processor_args.append("--overwrite")
    return process_video_main(processor_args)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return process(args)
    except ToolError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nCapture interrupted.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
