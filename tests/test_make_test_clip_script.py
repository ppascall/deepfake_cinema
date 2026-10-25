import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import common


def test_ffprobe_handles_invalid_utf8_bytes(monkeypatch):
    class FakeCompletedProcess:
        returncode = 0
        stdout = (
            b'\x9d{"streams": [{"codec_type": "video", "width": 640, "height": 360, '
            b'"avg_frame_rate": "30/1", "duration": "1.0", "nb_frames": "30", '
            b'"disposition": {"attached_pic": 0}}, {"codec_type": "audio", "duration": "1.0"}], '
            b'"format": {"duration": "1.0"}}'
        )
        stderr = b""

    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: FakeCompletedProcess())
    result = common.probe_video(Path("input/movie.mp4"))

    assert result.width == 640
    assert result.height == 360
    assert result.duration == 1.0
    assert result.nb_frames == 30


def test_make_test_clip_accepts_video_argument(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "make_test_clip.ps1"
    video_path = repo_root / "input" / "movie.mp4"
    output_path = tmp_path / "clip.mp4"

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Video",
            str(video_path),
            "-Output",
            str(output_path),
            "-Start",
            "00:00:00",
            "-Duration",
            "1",
            "-Height",
            "360",
            "-Force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert output_path.exists()


def test_make_test_clip_default_start_uses_safe_value(tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    script_path = repo_root / "scripts" / "make_test_clip.ps1"
    output_path = tmp_path / "default_clip.mp4"

    result = subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
            "-Output",
            str(output_path),
            "-Force",
        ],
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert output_path.exists()
