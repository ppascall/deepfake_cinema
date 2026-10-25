"""Offline, CPU-only face swap for a prerecorded video.

    python -m src.process_video --video input/test_clip.mp4 --source-face input/person.jpg \\
        --model models/inswapper_128.onnx --output output/test_result.mp4 --device cpu

Pipeline: OpenCV reads frames -> largest face per frame is swapped -> frames are piped to
FFmpeg (silent temp video) -> original audio is muxed back -> final MP4.
Only use footage and photos you have permission to process.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from .common import (DEFAULT_DETECTOR, DEFAULT_OUTPUT, DEFAULT_RECOGNIZER, DEFAULT_SOURCE_FACE,
                     DEFAULT_SWAPPER, DEFAULT_VIDEO, ToolError, check_free_space, fmt_duration,
                     probe_video, require_tool, resolve_output_path, run_ffmpeg, show)
from .detect_faces import (FaceAnalyzer, import_runtime, largest_face, load_onnx_model,
                           load_source_face)

PROGRESS_INTERVAL = 3.0   # seconds between progress lines
MAX_CONSECUTIVE_ERRORS = 50


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Swap one face in a video (offline, CPU).",
                                 formatter_class=argparse.ArgumentDefaultsHelpFormatter)
    ap.add_argument("--video", type=Path, default=DEFAULT_VIDEO, help="Input video (never modified).")
    ap.add_argument("--source-face", type=Path, default=DEFAULT_SOURCE_FACE,
                    help="Photo of the consenting person whose face is inserted.")
    ap.add_argument("--model", type=Path, default=DEFAULT_SWAPPER, help="Authorized swapper ONNX model.")
    ap.add_argument("--det-model", type=Path, default=DEFAULT_DETECTOR, help="Face detector ONNX model.")
    ap.add_argument("--rec-model", type=Path, default=DEFAULT_RECOGNIZER, help="Face recognition ONNX model.")
    ap.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="Output .mp4 path.")
    ap.add_argument("--device", choices=["cpu"], default="cpu", help="Only CPU inference is supported.")
    ap.add_argument("--max-seconds", type=float, default=5.0,
                    help="Process only the first N seconds (0 = whole video).")
    ap.add_argument("--fps", type=float, default=20.0,
                    help="Output frame rate; frames are dropped by timestamp, never duplicated "
                         "(0 = keep the source rate).")
    ap.add_argument("--scale-height", type=int, default=720,
                    help="Downscale to this height, keeping aspect ratio (never upscales; 0 = keep original).")
    ap.add_argument("--det-size", type=int, default=640,
                    help="Detector input size in pixels; smaller is faster, larger finds smaller faces.")
    ap.add_argument("--overwrite", action="store_true", help="Allow replacing an existing output file.")
    ap.add_argument("--keep-temp", action="store_true", help="Keep temporary files after success.")
    ap.add_argument("--dry-run", action="store_true",
                    help="Check environment, inputs and models, print the plan, and exit without processing.")
    return ap


def validate_args(args) -> None:
    if args.max_seconds < 0:
        raise ToolError("--max-seconds must be greater than 0 (or 0 for the whole video).")
    if args.fps < 0:
        raise ToolError("--fps must be greater than 0 (or 0 to keep the source frame rate).")
    if args.scale_height < 0:
        raise ToolError("--scale-height must be 0 or a positive number of pixels.")
    if args.scale_height and args.scale_height < 64:
        raise ToolError("--scale-height below 64 pixels is too small for face detection.")
    if args.det_size < 160 or args.det_size % 32:
        raise ToolError("--det-size must be a multiple of 32 and at least 160 (e.g. 320, 480, 640).")
    if args.output.suffix.lower() != ".mp4":
        raise ToolError("--output must end in .mp4")


def target_size(width: int, height: int, scale_height: int) -> tuple[int, int]:
    """Output size with the aspect ratio kept and both sides even (required by yuv420p)."""
    if scale_height and height > scale_height:
        width, height = round(width * scale_height / height), scale_height
    return max(width & ~1, 2), max(height & ~1, 2)


def plan_frames(info, args) -> tuple[float, int, int | None]:
    """Return (output fps, frames to produce, frame limit or None)."""
    out_fps = min(args.fps, info.fps) if args.fps else info.fps
    total = max(int(round(info.nb_frames * out_fps / info.fps)), 1)
    limit = int(round(args.max_seconds * out_fps)) if args.max_seconds else None
    return out_fps, (min(total, limit) if limit else total), limit


def print_plan(info, out_fps: float, frames_planned: int, out_w: int, out_h: int) -> None:
    seconds = frames_planned / out_fps
    print(f"Video:   {show(info.path)}  {info.width}x{info.height} @ {info.fps:.2f} fps, "
          f"{info.duration:.1f}s, audio: {'yes' if info.has_audio else 'no'}")
    print(f"Plan:    {frames_planned} frames ({fmt_duration(seconds)} of video) at {out_w}x{out_h}, "
          f"{out_fps:g} fps output")
    print("Time:    frames / (processed frames per second). Example: "
          + ", ".join(f"{r:g} fps -> {fmt_duration(frames_planned / r)}" for r in (1, 2, 5))
          + " (plus encoding). Real speed is measured during the run.")


def open_silent_writer(path: Path, log_path: Path, width: int, height: int, fps: float):
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{width}x{height}", "-r", f"{fps:.6f}", "-i", "-",
           "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p", str(path)]
    log = open(log_path, "wb")
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=log)
    return proc, log


def mux_audio(silent: Path, info, out_path: Path, duration_limit: float | None) -> None:
    args = ["-y", "-i", str(silent)]
    if info.has_audio:
        args += ["-i", str(info.path), "-map", "0:v:0", "-map", "1:a:0",
                 "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest"]
        if duration_limit is not None:
            args += ["-t", f"{duration_limit:.6f}"]
    else:
        print("Note: the input video has no audio track; the output will be silent.")
        args += ["-c:v", "copy"]
    args += ["-movflags", "+faststart", str(out_path)]
    run_ffmpeg(args, "adding the original audio")


def process(args) -> int:
    validate_args(args)
    cv2, _ = import_runtime()
    require_tool("ffmpeg")
    info = probe_video(args.video)

    if args.dry_run:
        from .environment import print_report, run_checks
        out_dir = args.output.expanduser().resolve().parent
        checks = run_checks(args.video, args.source_face, args.model, args.det_model, args.rec_model, out_dir)
        ready = print_report(checks)
        out_fps, frames_planned, _ = plan_frames(info, args)
        out_w, out_h = target_size(info.width, info.height, args.scale_height)
        print()
        print_plan(info, out_fps, frames_planned, out_w, out_h)
        if args.output.exists() and not args.overwrite:
            print(f"Note: {show(args.output)} exists; a real run needs --overwrite or another --output.")
        print("Dry run: nothing was processed.")
        return 0 if ready else 1

    output = resolve_output_path(args.output, args.overwrite, protect=[args.video, args.source_face])
    output.parent.mkdir(parents=True, exist_ok=True)

    print("Reminder: only process footage and face photos you have permission to use.")
    analyzer = FaceAnalyzer(args.det_model, args.rec_model, det_size=args.det_size)
    swapper = load_onnx_model(args.model, "face swapper")
    if type(swapper).__name__ != "INSwapper":
        raise ToolError(
            f"{show(args.model)} is not an InSwapper-style model (got {type(swapper).__name__}). "
            "See README.md, 'Models and licensing'."
        )
    source_face, note = load_source_face(analyzer, args.source_face)
    if note:
        print(f"Warning: {note}")

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise ToolError(f"OpenCV could not open {show(args.video)}. Try re-encoding it with ffmpeg (H.264 MP4).")
    ok, frame = cap.read()
    if not ok or frame is None:
        cap.release()
        raise ToolError(f"OpenCV could not decode any frame from {show(args.video)}.")

    # Use the decoded frame size (OpenCV applies rotation metadata), not the ffprobe size.
    out_w, out_h = target_size(frame.shape[1], frame.shape[0], args.scale_height)
    out_fps, frames_planned, frame_limit = plan_frames(info, args)
    print_plan(info, out_fps, frames_planned, out_w, out_h)
    if info.vfr_suspected:
        print("Warning: variable frame rate detected; audio may drift. Convert to constant frame rate first.")
    check_free_space(output.parent, int(3e6 * frames_planned / out_fps) + int(2e8))

    tmp_dir = output.parent / f".tmp_{output.stem}_{os.getpid()}"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    silent = tmp_dir / "silent.mp4"
    writer_log = tmp_dir / "ffmpeg_writer.log"
    writer, log_file = open_silent_writer(silent, writer_log, out_w, out_h, out_fps)

    def next_frame():
        ok, f = cap.read()
        return f if ok else None

    counts = dict(read=0, swapped=0, no_face=0, errors=0)  # "read" = frames processed (output frames)
    consecutive_errors = 0
    src_index = 0
    start = last_report = time.monotonic()
    success = False
    try:
        while frame is not None:
            # Keep a source frame only when its timestamp reaches the next output frame's timestamp.
            if src_index * out_fps + 1e-6 < counts["read"] * info.fps:
                src_index += 1
                frame = next_frame()
                continue
            if (frame.shape[1], frame.shape[0]) != (out_w, out_h):
                frame = cv2.resize(frame, (out_w, out_h), interpolation=cv2.INTER_AREA)
            counts["read"] += 1
            try:
                target = largest_face(analyzer.detect(frame))
                if target is None:
                    counts["no_face"] += 1
                else:
                    frame = swapper.get(frame, target, source_face, paste_back=True)
                    counts["swapped"] += 1
                consecutive_errors = 0
            except Exception as exc:  # frame is left unchanged
                counts["errors"] += 1
                consecutive_errors += 1
                if counts["errors"] <= 5:
                    print(f"\nError on frame {counts['read']}: {exc} (frame left unchanged)")
                if consecutive_errors >= MAX_CONSECUTIVE_ERRORS:
                    raise ToolError(f"{MAX_CONSECUTIVE_ERRORS} consecutive frame errors; last: {exc}")
            try:
                writer.stdin.write(frame.tobytes())
            except BrokenPipeError:
                raise ToolError(f"FFmpeg stopped unexpectedly:\n{writer_log.read_text(errors='replace')[-600:]}")

            now = time.monotonic()
            if now - last_report >= PROGRESS_INTERVAL:
                last_report = now
                print_progress(counts, frames_planned, now - start)
            if frame_limit and counts["read"] >= frame_limit:
                break
            src_index += 1
            frame = next_frame()

        writer.stdin.close()
        if writer.wait() != 0:
            raise ToolError(f"FFmpeg failed to write the temporary video:\n{writer_log.read_text(errors='replace')[-600:]}")
        elapsed = time.monotonic() - start
        print_progress(counts, frames_planned, elapsed, final=True)
        if counts["read"] == 0:
            raise ToolError("No frames were processed.")

        print("Adding original audio...")
        final_tmp = tmp_dir / "final.mp4"
        limit = counts["read"] / out_fps if frame_limit else None
        mux_audio(silent, info, final_tmp, limit)
        os.replace(final_tmp, output)
        success = True
    except KeyboardInterrupt:
        print(f"\nInterrupted. Temporary files kept in {show(tmp_dir)}")
        return 130
    finally:
        cap.release()
        if writer.poll() is None:
            writer.kill()
            writer.wait()
        log_file.close()
        if success and not args.keep_temp:
            shutil.rmtree(tmp_dir, ignore_errors=True)
        elif not success:
            print(f"Temporary files kept for inspection: {show(tmp_dir)} (see ffmpeg_writer.log)")

    swapped_pct = 100 * counts["swapped"] / counts["read"]
    print(f"\nDone: {show(output)}")
    print(f"Frames: {counts['read']}  swapped: {counts['swapped']} ({swapped_pct:.0f}%)  "
          f"no face: {counts['no_face']}  errors: {counts['errors']}  "
          f"time: {fmt_duration(elapsed)}  speed: {counts['read'] / elapsed:.2f} fps")
    if counts["swapped"] == 0:
        print("Warning: no face was swapped. Check the video with: python -m src.detect_faces --video ... --time N")
    return 0


def print_progress(counts, total, elapsed, final=False) -> None:
    done = counts["read"]
    speed = done / elapsed if elapsed > 0 else 0.0
    pct = f"{100 * done / total:5.1f}%" if total else "  ?  "
    eta = f"  ETA {fmt_duration((total - done) / speed)}" if speed > 0 and total > done and not final else ""
    print(f"[{pct}] frame {done}/{total}  {speed:.2f} fps  elapsed {fmt_duration(elapsed)}{eta}  "
          f"swapped {counts['swapped']}  no-face {counts['no_face']}  errors {counts['errors']}", flush=True)


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return process(args)
    except ToolError as exc:
        print(f"\nERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
