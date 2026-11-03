from pathlib import Path, PurePosixPath
from types import SimpleNamespace

from src import runcrate_worker


def make_project(root: Path):
    (root / "src").mkdir(parents=True)
    for source_file in ("__init__.py", "common.py", "detect_faces.py", "process_video.py"):
        (root / "src" / source_file).write_text("")
    (root / "src" / "capture_face.py").write_text("")
    (root / "src" / "runcrate_worker.py").write_text("")
    (root / "requirements.txt").write_text("onnxruntime\n")
    (root / "requirements-gpu.txt").write_text("onnxruntime-gpu\n")
    for model in (
        "models/inswapper_128.onnx",
        "models/buffalo_l/det_10g.onnx",
        "models/buffalo_l/w600k_r50.onnx",
    ):
        model_path = root / model
        model_path.parent.mkdir(parents=True, exist_ok=True)
        model_path.write_bytes(b"model" * 512)
    video = root / "selected.mp4"
    face = root / "face.jpg"
    video.write_bytes(b"selected scene")
    face.write_bytes(b"selected face")
    return video, face


def test_stage_project_copies_processor_models_and_only_selected_media(tmp_path):
    project = tmp_path / "project"
    video, face = make_project(project)
    (project / "input").mkdir()
    (project / "input" / "whole-film.mp4").write_bytes(b"full source film")

    staged, remote_video, remote_face = runcrate_worker.stage_project(
        tmp_path / "staging", video, face, project,
    )

    assert (staged / "src" / "__init__.py").is_file()
    assert (staged / "src" / "process_video.py").is_file()
    assert not (staged / "src" / "capture_face.py").exists()
    assert not (staged / "src" / "runcrate_worker.py").exists()
    assert (staged / "models" / "inswapper_128.onnx").is_file()
    assert (staged / remote_video).read_bytes() == b"selected scene"
    assert (staged / remote_face).read_bytes() == b"selected face"
    assert not (staged / "input" / "whole-film.mp4").exists()
    assert not (staged / "output").exists()


def test_instance_waits_for_deploying_then_accepts_running(monkeypatch):
    responses = iter(({"status": "deploying"}, {"status": "running"}))
    sleeps = []
    monkeypatch.setattr(runcrate_worker, "instance_status", lambda instance: next(responses))
    monkeypatch.setattr(runcrate_worker.time, "sleep", sleeps.append)

    runcrate_worker.ensure_instance_running("face-swap")

    assert sleeps == [runcrate_worker.POLL_INTERVAL_SECONDS]


def test_remote_command_uses_cuda_and_selected_scene():
    command = runcrate_worker.remote_process_command(
        PurePosixPath("/root/job"),
        PurePosixPath("input/video.mp4"),
        PurePosixPath("input/face.jpg"),
        PurePosixPath("output.mp4"),
        5.5,
    )

    uninstall_both = command.index("pip uninstall -y onnxruntime onnxruntime-gpu")
    install_requirements = command.index("pip install --no-cache-dir -r /root/job/requirements.txt")
    uninstall_cpu = command.index("pip uninstall -y onnxruntime >/dev/null")
    install_gpu = command.index("pip install --no-cache-dir -r /root/job/requirements-gpu.txt")

    assert uninstall_both < install_requirements < uninstall_cpu < install_gpu
    assert "--device cuda" in command
    assert "--max-seconds 5.5" in command
    assert "/root/job/input/video.mp4" in command
    assert "runcrate" not in command


def test_job_reuses_instance_and_leaves_it_running_by_default(tmp_path, monkeypatch, capsys):
    project = tmp_path / "project"
    video, face = make_project(project)
    output = tmp_path / "output" / "result.mp4"
    monkeypatch.setattr(runcrate_worker, "PROJECT_ROOT", project)
    monkeypatch.setattr(runcrate_worker, "ensure_instance_running", lambda instance: None)
    calls = []

    def fake_run_cli(command, action):
        calls.append((command, action))
        if command[1] == "cp" and command[2].startswith("face-swap:") and command[2].endswith("output.mp4"):
            Path(command[-1]).write_bytes(b"processed scene")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    ssh_commands = []
    cleanup_commands = []
    monkeypatch.setattr(runcrate_worker, "run_cli", fake_run_cli)
    monkeypatch.setattr(runcrate_worker, "ensure_remote_models", lambda *args: None)
    monkeypatch.setattr(
        runcrate_worker, "run_ssh",
        lambda instance, command: ssh_commands.append((instance, command)),
    )
    monkeypatch.setattr(
        runcrate_worker, "run_ssh_args",
        lambda instance, *command: cleanup_commands.append((instance, command)),
    )
    args = SimpleNamespace(
        video=video, source_face=face, output=output, max_seconds=8.0,
        instance="face-swap", shutdown_after_job=False,
    )

    result = runcrate_worker.process(args)

    assert result == output
    assert output.read_bytes() == b"processed scene"
    assert calls[0][0][:3] == ["runcrate", "cp", "-r"]
    assert len(ssh_commands) == 1
    assert "--device cuda" in ssh_commands[0][1]
    assert cleanup_commands[0][0] == "face-swap"
    assert cleanup_commands[0][1][:3] == ("rm", "-rf", "--")
    assert cleanup_commands[0][1][3].startswith("/root/deepfake_cinema_")
    assert not any(command[:3] == ["runcrate", "instances", "delete"] for command, _ in calls)
    assert "Leaving Runcrate instance 'face-swap' running" in capsys.readouterr().out


def test_shutdown_is_opt_in(monkeypatch, tmp_path):
    commands = []
    monkeypatch.setattr(runcrate_worker, "validate_args", lambda args: None)
    monkeypatch.setattr(runcrate_worker, "ensure_instance_running", lambda instance: None)

    def fake_stage(root, video, face, project_root, include_models=True):
        staged = root / "deepfake_cinema_test"
        staged.mkdir()
        return staged, PurePosixPath("input/video.mp4"), PurePosixPath("input/face.jpg")

    monkeypatch.setattr(runcrate_worker, "stage_project", fake_stage)
    monkeypatch.setattr(runcrate_worker, "ensure_remote_models", lambda *args: None)

    def fake_run_cli(command, action):
        commands.append(command)
        if command[1] == "cp" and command[2].startswith("face-swap:") and command[2].endswith("/output.mp4"):
            Path(command[-1]).write_bytes(b"result")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(runcrate_worker, "run_cli", fake_run_cli)
    monkeypatch.setattr(runcrate_worker, "run_ssh", lambda *args: None)
    monkeypatch.setattr(runcrate_worker, "run_ssh_args", lambda *args: None)
    args = SimpleNamespace(
        video=tmp_path / "video.mp4", source_face=tmp_path / "face.jpg",
        output=tmp_path / "out" / "result.mp4", max_seconds=None,
        instance="face-swap", shutdown_after_job=True,
    )
    args.video.write_bytes(b"video")
    args.source_face.write_bytes(b"face")

    runcrate_worker.process(args)

    assert commands[-1] == ["runcrate", "instances", "delete", "face-swap"]


def test_models_are_uploaded_only_when_missing_on_the_instance(monkeypatch, tmp_path):
    for relative in ("models/inswapper_128.onnx", "models/buffalo_l/det_10g.onnx", "models/buffalo_l/w600k_r50.onnx"):
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"x" * 2048)
    uploads = []
    monkeypatch.setattr(runcrate_worker, "run_cli", lambda command, action: uploads.append(command))
    monkeypatch.setattr(runcrate_worker, "run_ssh_args", lambda *args: None)

    monkeypatch.setattr(runcrate_worker, "remote_models_cached", lambda instance, sizes: True)
    runcrate_worker.ensure_remote_models("face-swap", tmp_path / "stage", tmp_path)
    assert uploads == []

    (tmp_path / "stage").mkdir()
    monkeypatch.setattr(runcrate_worker, "remote_models_cached", lambda instance, sizes: False)
    runcrate_worker.ensure_remote_models("face-swap", tmp_path / "stage", tmp_path)
    assert uploads and uploads[0][:3] == ["runcrate", "cp", "-r"]
