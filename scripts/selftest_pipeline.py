#!/usr/bin/env python3
"""Pipeline self-test with stub face models (no real weights, no real faces).

Builds a synthetic video with audio, runs the real process_video pipeline with a fake detector
and a fake swapper, then checks frame count, audio and duration of the result. It verifies the
plumbing (reading, scaling, writing, audio mux, overwrite protection), not swap quality.

    python scripts/selftest_pipeline.py
"""
import io
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from src import process_video  # noqa: E402
from src.common import probe_video  # noqa: E402


class FakeAnalyzer:
    def __init__(self, *a, **k):
        pass

    def detect(self, img):
        h, w = img.shape[:2]
        small = SimpleNamespace(bbox=np.array([0, 0, 10, 10.0]), kps=None, det_score=0.9)
        big = SimpleNamespace(bbox=np.array([w * .25, h * .25, w * .75, h * .75]), kps=None, det_score=0.9)
        return [small, big]


class INSwapper:  # name matters: process_video checks the class name
    def get(self, img, target, source, paste_back=True):
        x1, y1, x2, y2 = (int(v) for v in target.bbox)
        out = img.copy()
        out[y1:y2, x1:x2] = (0, 0, 255)
        return out


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "synthetic.mp4"
        subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "lavfi", "-i",
                        "testsrc=size=640x360:rate=25", "-f", "lavfi", "-i", "sine=frequency=440",
                        "-t", "4", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(src)],
                       check=True)
        photo = tmp / "person.jpg"
        photo.write_bytes(b"x")
        model = tmp / "swapper.onnx"
        model.write_bytes(b"x")
        out = tmp / "out" / "result.mp4"

        process_video.FaceAnalyzer = FakeAnalyzer
        process_video.load_onnx_model = lambda *a, **k: INSwapper()
        process_video.load_source_face = lambda *a, **k: (SimpleNamespace(), None)

        args = ["--video", str(src), "--source-face", str(photo), "--model", str(model),
                "--output", str(out), "--scale-height", "180", "--max-seconds", "3"]
        assert process_video.main(args) == 0, "first run failed"

        info = probe_video(out)
        assert info.has_audio, "output has no audio"
        assert (info.width, info.height) == (320, 180), f"unexpected size {info.width}x{info.height}"
        assert info.nb_frames == 75, f"expected 75 frames, got {info.nb_frames}"
        assert abs(info.duration - 3.0) < 0.15, f"unexpected duration {info.duration}"
        assert not list(out.parent.glob(".tmp_*")), "temporary files were not cleaned up"

        sys.stdin = io.StringIO()  # non-interactive: no filename prompt
        assert process_video.main(args) == 2, "existing output must not be overwritten silently"
        assert process_video.main(args + ["--overwrite"]) == 0, "--overwrite run failed"
        assert process_video.main(args + ["--dry-run", "--overwrite"]) in (0, 1)
    print("\nSELFTEST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
