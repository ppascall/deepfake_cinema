#!/usr/bin/env python3
"""Verify the environment without processing any movie.

    python scripts/check_environment.py
    python scripts/check_environment.py --inputs      # also check input/movie.mp4 and input/person.jpg
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.common import (DEFAULT_DETECTOR, DEFAULT_RECOGNIZER, DEFAULT_SOURCE_FACE,  # noqa: E402
                        DEFAULT_SWAPPER, DEFAULT_VIDEO, PROJECT_ROOT)
from src.environment import print_report, run_checks  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inputs", action="store_true", help="Also check the default input video and source photo.")
    ap.add_argument("--video", type=Path, help="Check this video (implies --inputs for the video).")
    ap.add_argument("--source-face", type=Path, help="Check this source photo.")
    ap.add_argument("--model", type=Path, default=DEFAULT_SWAPPER)
    ap.add_argument("--det-model", type=Path, default=DEFAULT_DETECTOR)
    ap.add_argument("--rec-model", type=Path, default=DEFAULT_RECOGNIZER)
    ap.add_argument("--output-dir", type=Path, default=PROJECT_ROOT / "output")
    args = ap.parse_args()

    video = args.video or (DEFAULT_VIDEO if args.inputs else None)
    photo = args.source_face or (DEFAULT_SOURCE_FACE if args.inputs else None)
    checks = run_checks(video, photo, args.model, args.det_model, args.rec_model, args.output_dir)
    return 0 if print_report(checks) else 1


if __name__ == "__main__":
    sys.exit(main())
