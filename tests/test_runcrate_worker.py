import io
import sys
from pathlib import Path
from types import SimpleNamespace

from src import runcrate_worker


def make_project(root: Path):
    (root / "src").mkdir(parents=True)
    (root / "src" / "__init__.py").write_text("")
    (root / "requirements.txt").write_text("onnxruntime\n")
    (root / "requirements-gpu.txt").write_text("onnxruntime-gpu\n")
    (root / "models" / "buffalo_l").mkdir(parents=True)
    (root / "models" / "inswapper_128.onnx").write_bytes(b"swapper")
    (root / "models" / "buffalo_l" / "det_10g.onnx").write_bytes(b"detector")
    (root / "models" / "buffalo_l" / "w600k_r50.onnx").write_bytes(b"recognizer")
    video = root / "clip.mp4"
    face = root / "face.jpg"
    video.write_bytes(b"video")
    face.write_bytes(b"face")
    return video, face


def test_stage_project_copies_only_processor_and_selected_assets(tmp_path):
    video, face = make_project(tmp_path / "project")
    staged, remote_video, remote_face = runcrate_worker.stage_project(
        tmp_path / "staging", video, face, tmp_path / "project",
    )

    assert (staged / "src" / "__init__.py").is_file()
    assert (staged / "models" / "inswapper_128.onnx").read_bytes() == b"swapper"
    assert (staged / remote_video).read_bytes() == b"video"
    assert (staged / remote_face).read_bytes() == b"face"
    assert not (staged / "output").exists()


def test_instance_lifecycle_uploads_runs_downloads_and_terminates(tmp_path, monkeypatch):
    project = tmp_path / "project"
    video, face = make_project(project)
    key = tmp_path / "id_ed25519"
    key.write_text("private key")
    output = tmp_path / "output" / "result.mp4"
    monkeypatch.setattr(runcrate_worker, "PROJECT_ROOT", project)
    monkeypatch.setattr(runcrate_worker.time, "sleep", lambda seconds: None)

    calls = {"create": [], "status": [], "terminate": [], "ssh": [], "scp": []}

    class FakeInstances:
        def create(self, **kwargs):
            calls["create"].append(kwargs)
            return SimpleNamespace(id="instance-123")

        def get_status(self, instance_id):
            calls["status"].append(instance_id)
            return SimpleNamespace(status="running", ip="192.0.2.10", ssh_port=2222)

        def terminate(self, instance_id):
            calls["terminate"].append(instance_id)

    class FakeClient:
        instances = FakeInstances()

        def __init__(self, api_key):
            assert api_key == "rc_live_test"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

    monkeypatch.setitem(sys.modules, "runcrate", SimpleNamespace(Runcrate=FakeClient))

    class FakeSshProcess:
        stdout = io.StringIO("remote progress\n")

        def __init__(self, command, **kwargs):
            calls["ssh"].append(command)

        def wait(self):
            return 0

    def fake_run(command, **kwargs):
        calls["scp"].append(command)
        if command[0] == "scp" and command[-2].startswith("root@"):
            Path(command[-1]).write_bytes(b"processed video")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runcrate_worker.subprocess, "Popen", FakeSshProcess)
    monkeypatch.setattr(runcrate_worker.subprocess, "run", fake_run)
    monkeypatch.setenv("RUNCRATE_API_KEY", "rc_live_test")
    args = SimpleNamespace(
        video=video,
        source_face=face,
        output=output,
        max_seconds=12.5,
        gpu_type="RTX4090",
        ssh_key_id="key_123",
        ssh_private_key=key,
        max_runtime_minutes=60,
    )

    result = runcrate_worker.process(args)

    assert result == output
    assert output.read_bytes() == b"processed video"
    assert calls["create"][0]["ssh_key_id"] == "key_123"
    assert calls["create"][0]["gpu_count"] == 1
    assert calls["create"][0]["gpu_type"] == "RTX4090"
    assert calls["status"] == ["instance-123"]
    assert calls["terminate"] == ["instance-123"]
    assert any("--device cuda" in command[-1] for command in calls["ssh"])
    assert len(calls["scp"]) == 2