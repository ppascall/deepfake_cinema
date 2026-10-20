"""Face detection / embedding (InsightFace models via ONNX Runtime, CPU only).

Run as a script to see which face would be picked in a photo or a video frame:

    python -m src.detect_faces --image input/person.jpg
    python -m src.detect_faces --video input/test_clip.mp4 --time 3 --annotate output/frame_faces.jpg
"""
from __future__ import annotations

import argparse
import contextlib
import io
import sys
from pathlib import Path

from .common import (CPU_PROVIDERS, DEFAULT_DETECTOR, DEFAULT_RECOGNIZER, ToolError,
                     resolve_output_path, show)

MODEL_HELP = (
    "See README.md, section 'Models and licensing', for where to obtain authorized weights "
    "and the exact path to place them. This tool never downloads model files."
)


def import_runtime():
    """Import heavy dependencies with actionable error messages."""
    try:
        import cv2
        import numpy as np
    except ImportError as exc:
        raise ToolError(
            f"Missing Python package ({exc.name}). Activate the virtualenv and run: "
            "python -m pip install -r requirements.txt"
        )
    try:
        import onnxruntime  # noqa: F401
        import insightface  # noqa: F401
    except ImportError as exc:
        raise ToolError(
            f"Missing Python package ({exc.name}). Activate the virtualenv and run: "
            "python -m pip install -r requirements.txt\n"
            "If insightface fails to build, see 'Troubleshooting' in README.md."
        )
    return cv2, np


def require_model_file(path: Path, what: str) -> None:
    if not path.exists():
        raise ToolError(f"Missing {what} model file: {show(path)}\n{MODEL_HELP}")
    if not path.is_file() or path.stat().st_size < 1024:
        raise ToolError(f"{what} model file looks invalid (not a file or too small): {show(path)}\n{MODEL_HELP}")


def load_onnx_model(path: Path, what: str):
    """Load an InsightFace-compatible ONNX model on the CPU provider only."""
    import_runtime()
    require_model_file(path, what)
    from insightface.model_zoo import get_model
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            model = get_model(str(path), providers=CPU_PROVIDERS)
    except Exception as exc:
        raise ToolError(
            f"Could not load {what} model {show(path)}: {exc}\n"
            "The file may be corrupt, incomplete, or not an ONNX model compatible with this tool."
        )
    if model is None:
        raise ToolError(f"{show(path)} is not a model type InsightFace recognizes ({what}).")
    return model


class FaceAnalyzer:
    """SCRFD detector + ArcFace recognizer."""

    def __init__(self, detector_path: Path = DEFAULT_DETECTOR,
                 recognizer_path: Path = DEFAULT_RECOGNIZER,
                 det_size: int = 640, det_thresh: float = 0.5):
        self.det = load_onnx_model(detector_path, "face detector")
        if not hasattr(self.det, "detect"):
            raise ToolError(f"{show(detector_path)} is not a face-detection model (expected SCRFD det_10g.onnx).")
        self.rec = load_onnx_model(recognizer_path, "face recognizer")
        if not hasattr(self.rec, "get_feat"):
            raise ToolError(f"{show(recognizer_path)} is not a face-recognition model (expected ArcFace w600k_r50.onnx).")
        self.det.prepare(-1, input_size=(det_size, det_size), det_thresh=det_thresh)
        self.rec.prepare(-1)

    def detect(self, img):
        """Return faces (insightface Face objects with bbox, kps, det_score), no embedding yet."""
        from insightface.app.common import Face
        bboxes, kpss = self.det.detect(img, max_num=0, metric="default")
        if bboxes is None or bboxes.shape[0] == 0:
            return []
        if kpss is None:
            raise ToolError("The detector model returned no landmarks; use SCRFD det_10g.onnx.")
        return [Face(bbox=bboxes[i, :4], kps=kpss[i], det_score=bboxes[i, 4])
                for i in range(bboxes.shape[0])]

    def embed(self, img, face):
        self.rec.get(img, face)
        return face


def face_area(face) -> float:
    x1, y1, x2, y2 = face.bbox[:4]
    return float((x2 - x1) * (y2 - y1))


def largest_face(faces):
    return max(faces, key=face_area) if faces else None


def load_source_face(analyzer: FaceAnalyzer, image_path: Path):
    """Detect the source person's face in a photo and compute its embedding."""
    cv2, _ = import_runtime()
    if not image_path.exists():
        raise ToolError(f"Source face photo not found: {show(image_path)}")
    img = cv2.imread(str(image_path))
    if img is None:
        raise ToolError(f"Could not read {show(image_path)} as an image (supported: JPG, PNG, ...).")
    faces = analyzer.detect(img)
    if not faces:
        raise ToolError(
            f"No face detected in {show(image_path)}. Use a clear, front-facing, evenly lit, "
            "sharp photo with the face at least ~200 px wide, without sunglasses or heavy occlusion."
        )
    face = largest_face(faces)
    note = None
    if len(faces) > 1:
        note = f"{len(faces)} faces found in {show(image_path)}; using the largest one."
    analyzer.embed(img, face)
    return face, note


def _read_video_frame(video: Path, seconds: float):
    cv2, _ = import_runtime()
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise ToolError(f"OpenCV could not open {show(video)}.")
    cap.set(cv2.CAP_PROP_POS_MSEC, seconds * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise ToolError(f"Could not read a frame at {seconds}s from {show(video)} (is the time inside the video?).")
    return frame


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Detect faces in an image or a video frame.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", type=Path)
    src.add_argument("--video", type=Path)
    ap.add_argument("--time", type=float, default=0.0, help="Seconds into the video (with --video).")
    ap.add_argument("--annotate", type=Path, help="Save an image with boxes; the largest face is green.")
    ap.add_argument("--overwrite", action="store_true")
    ap.add_argument("--det-model", type=Path, default=DEFAULT_DETECTOR)
    ap.add_argument("--rec-model", type=Path, default=DEFAULT_RECOGNIZER)
    args = ap.parse_args(argv)

    try:
        cv2, _ = import_runtime()
        out_path = resolve_output_path(args.annotate, args.overwrite) if args.annotate else None
        analyzer = FaceAnalyzer(args.det_model, args.rec_model)
        if args.image:
            img = cv2.imread(str(args.image))
            if img is None:
                raise ToolError(f"Could not read image: {show(args.image)}")
        else:
            img = _read_video_frame(args.video, args.time)
        faces = analyzer.detect(img)
        best = largest_face(faces)
        print(f"Image size: {img.shape[1]}x{img.shape[0]}  faces: {len(faces)}")
        for i, f in enumerate(sorted(faces, key=face_area, reverse=True)):
            x1, y1, x2, y2 = (int(v) for v in f.bbox)
            tag = "  <- largest (would be swapped)" if f is best else ""
            print(f"  #{i}: box=({x1},{y1})-({x2},{y2}) size={x2 - x1}x{y2 - y1} score={f.det_score:.2f}{tag}")
        if out_path:
            for f in faces:
                x1, y1, x2, y2 = (int(v) for v in f.bbox)
                color = (0, 255, 0) if f is best else (0, 0, 255)
                cv2.rectangle(img, (x1, y1), (x2, y2), color, 2)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            cv2.imwrite(str(out_path), img)
            print(f"Annotated image saved to {show(out_path)}")
        return 0 if faces else 1
    except ToolError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
