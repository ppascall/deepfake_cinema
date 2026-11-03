import io
import json
import threading
import urllib.error
import urllib.request

from scripts import runcrate_bridge


class CompletedWorker:
    def __init__(self, _command, **kwargs):
        self.kwargs = kwargs
        self.stdout = io.StringIO("access_token=example-value\nworker ready\n")

    def wait(self):
        return 0


def request_json(url, token, body=None):
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(
        url,
        data=data,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def test_bridge_authenticates_worker_job_and_redacts_logs(tmp_path, monkeypatch):
    (tmp_path / "input").mkdir()
    (tmp_path / "output").mkdir()
    (tmp_path / "input" / "clip.mp4").write_bytes(b"clip")
    (tmp_path / "input" / "face.jpg").write_bytes(b"face")
    token = "local-test-bridge-token"
    worker = {}

    def fake_popen(command, **kwargs):
        worker["command"] = command
        worker["options"] = kwargs
        return CompletedWorker(command, **kwargs)

    monkeypatch.setattr(runcrate_bridge.subprocess, "Popen", fake_popen)
    monkeypatch.setenv("RUNCRATE_API_KEY", "must-not-be-forwarded")
    server = runcrate_bridge.create_server(
        token, host="127.0.0.1", port=0, project_root=tmp_path, python="/usr/bin/python3",
    )
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base_url = f"http://127.0.0.1:{server.server_port}"
    try:
        unauthorized_status, unauthorized = request_json(
            f"{base_url}/jobs",
            "wrong-token",
            {"video": "input/clip.mp4"},
        )
        assert unauthorized_status == 401
        assert "jobId" not in unauthorized

        status, submitted = request_json(
            f"{base_url}/jobs",
            token,
            {
                "instance": "face-swap",
                "video": "input/clip.mp4",
                "sourceFace": "input/face.jpg",
                "output": "output/result.mp4",
                "shutdownAfterJob": False,
            },
        )
        assert status == 202
        assert submitted["state"] == "running"
        assert worker["command"][0:3] == ["/usr/bin/python3", "-u", "-m"]
        assert worker["command"][3] == "src.runcrate_worker"
        assert worker["options"]["cwd"] == tmp_path.resolve()
        assert "RUNCRATE_API_KEY" not in worker["options"]["env"]

        status, snapshot = request_json(
            f"{base_url}/jobs/{submitted['jobId']}",
            token,
        )
        assert status == 200
        assert snapshot["state"] == "succeeded"
        assert snapshot["logs"][-2:] == ["[credential redacted]", "worker ready"]

        rejected_status, rejected = request_json(
            f"{base_url}/jobs",
            token,
            {
                "instance": "face-swap",
                "video": "../outside.mp4",
                "sourceFace": "input/face.jpg",
                "output": "output/result.mp4",
            },
        )
        assert rejected_status == 400
        assert "outside" in rejected["error"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)
