from __future__ import annotations

from collections import deque
from types import SimpleNamespace

import numpy as np
import pytest

from src import capture_face

cv2 = pytest.importorskip("cv2")


class FakeCapture:
    def __init__(self, opened=True, frames=None):
        self.opened = opened
        self.frames = iter(frames or [])
        self.released = False

    def isOpened(self):
        return self.opened

    def read(self):
        return next(self.frames, (False, None))

    def release(self):
        self.released = True


class FakeDetector:
    def __init__(self, faces=None):
        self.faces = faces or []

    def detect(self, frame):
        return self.faces


def face_box(box):
    return SimpleNamespace(bbox=np.array(box, dtype=float))


def configure_capture(monkeypatch, cap, keys, faces=None):
    destroyed = []
    monkeypatch.setattr(capture_face, "import_runtime", lambda: (cv2, np))
    monkeypatch.setattr(capture_face, "FaceDetector", lambda *args, **kwargs: FakeDetector(faces))
    monkeypatch.setattr(cv2, "VideoCapture", lambda source: cap)
    monkeypatch.setattr(cv2, "namedWindow", lambda *args: None)
    monkeypatch.setattr(cv2, "imshow", lambda *args: None)
    monkeypatch.setattr(cv2, "waitKey", lambda delay: keys.popleft())
    monkeypatch.setattr(cv2, "destroyAllWindows", lambda: destroyed.append(True))
    return destroyed


def synthetic_frame():
    return np.full((60, 80, 3), 127, dtype=np.uint8)


def test_camera_open_failure_reports_error_and_cleans_up(monkeypatch, capsys, tmp_path):
    cap = FakeCapture(opened=False)
    destroyed = configure_capture(monkeypatch, cap, deque())

    result = capture_face.main(["--output-dir", str(tmp_path)])

    assert result == 2
    assert "could not open" in capsys.readouterr().err.lower()
    assert cap.released
    assert destroyed == [True]


def test_frame_read_failure_reports_error_and_cleans_up(monkeypatch, capsys, tmp_path):
    cap = FakeCapture(frames=[(False, None)])
    destroyed = configure_capture(monkeypatch, cap, deque())

    result = capture_face.main(["--output-dir", str(tmp_path)])

    assert result == 2
    assert "could not read a frame" in capsys.readouterr().err.lower()
    assert cap.released
    assert destroyed == [True]


def test_no_face_does_not_save_even_when_save_key_is_pressed(monkeypatch, tmp_path):
    cap = FakeCapture(frames=[(True, synthetic_frame()), (True, synthetic_frame())])
    configure_capture(monkeypatch, cap, deque([ord("s"), ord("q")]))

    assert capture_face.main(["--output-dir", str(tmp_path)]) == 0
    assert not list(tmp_path.glob("*.jpg"))
    assert cap.released


def test_save_key_writes_reopenable_jpeg_and_creates_output_directory(monkeypatch, tmp_path):
    output_dir = tmp_path / "captured"
    cap = FakeCapture(frames=[(True, synthetic_frame()), (True, synthetic_frame())])
    configure_capture(monkeypatch, cap, deque([ord("s"), ord("q")]), [face_box([20, 10, 40, 30])])

    assert capture_face.main(["--output-dir", str(output_dir)]) == 0

    images = list(output_dir.glob("face_*.jpg"))
    assert len(images) == 1
    assert cv2.imread(str(images[0])) is not None
    assert cap.released


@pytest.mark.parametrize(
    "box",
    [
        (-5, 10, 12, 30),
        (65, 10, 85, 30),
        (20, -8, 40, 12),
        (20, 50, 40, 70),
    ],
)
def test_face_crop_clamps_to_each_frame_edge(box):
    frame = synthetic_frame()

    crop = capture_face.crop_face(frame, face_box(box))

    assert crop.size > 0
    assert crop.shape[0] <= frame.shape[0]
    assert crop.shape[1] <= frame.shape[1]


def test_existing_capture_file_is_never_overwritten(tmp_path):
    existing = tmp_path / "face_20261027_102015.jpg"
    existing.write_bytes(b"keep this file")

    saved = capture_face.save_jpeg(
        synthetic_frame(), tmp_path, "face", cv2, timestamp="20261027_102015"
    )

    assert existing.read_bytes() == b"keep this file"
    assert saved.name == "face_20261027_102015_1.jpg"
    assert cv2.imread(str(saved)) is not None


@pytest.mark.parametrize("key", [ord("q"), 27])
def test_quit_keys_release_camera_and_close_window(monkeypatch, tmp_path, key):
    cap = FakeCapture(frames=[(True, synthetic_frame())])
    destroyed = configure_capture(monkeypatch, cap, deque([key]))

    assert capture_face.main(["--output-dir", str(tmp_path)]) == 0
    assert cap.released
    assert destroyed == [True]


def test_stream_credentials_are_not_logged(monkeypatch, capsys, tmp_path):
    cap = FakeCapture(opened=False)
    configure_capture(monkeypatch, cap, deque())
    url = "rtsp://operator:secret@example.invalid/live"

    result = capture_face.main(["--stream-url", url, "--output-dir", str(tmp_path)])

    output = capsys.readouterr()
    assert result == 2
    assert "operator" not in output.out + output.err
    assert "secret" not in output.out + output.err
    assert url not in output.out + output.err
    assert cap.released


def test_capture_and_process_waits_then_passes_captured_photo_to_processor(monkeypatch, tmp_path):
    output_dir = tmp_path / "captured"
    cap = FakeCapture(frames=[(True, synthetic_frame())])
    configure_capture(monkeypatch, cap, deque([-1]), [face_box([20, 10, 40, 30])])
    times = iter([100.0, 105.0])
    monkeypatch.setattr(capture_face.time, "monotonic", lambda: next(times))
    processor_calls = []
    monkeypatch.setattr(
        "src.process_video.main",
        lambda argv: processor_calls.append(argv) or 0,
    )

    result = capture_face.main([
        "--capture-and-process",
        "--output-dir", str(output_dir),
        "--video", str(tmp_path / "movie.mp4"),
        "--output", str(tmp_path / "result.mp4"),
    ])

    assert result == 0
    assert len(processor_calls) == 1
    captured = next(output_dir.glob("face_*.jpg"))
    assert processor_calls[0][processor_calls[0].index("--source-face") + 1] == str(captured)
    assert processor_calls[0][processor_calls[0].index("--video") + 1] == str(tmp_path / "movie.mp4")
    assert processor_calls[0][processor_calls[0].index("--output") + 1] == str(tmp_path / "result.mp4")
    assert cap.released


def test_capture_and_process_cancel_does_not_run_processor(monkeypatch, tmp_path):
    cap = FakeCapture(frames=[(True, synthetic_frame())])
    configure_capture(monkeypatch, cap, deque([ord("q")]), [face_box([20, 10, 40, 30])])
    processor_calls = []
    monkeypatch.setattr(
        "src.process_video.main",
        lambda argv: processor_calls.append(argv) or 0,
    )

    result = capture_face.main(["--capture-and-process", "--output-dir", str(tmp_path)])

    assert result == 130
    assert processor_calls == []
    assert cap.released
    assert not list(tmp_path.glob("*.jpg"))
