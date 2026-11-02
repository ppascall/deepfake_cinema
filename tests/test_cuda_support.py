import sys
from types import SimpleNamespace

import pytest

from src import common, detect_faces, process_video


def test_cpu_is_default_and_cuda_is_selected(monkeypatch):
    assert process_video.build_parser().parse_args([]).device == "cpu"
    assert common.inference_providers("cpu") == ["CPUExecutionProvider"]

    preloaded = []
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CUDAExecutionProvider", "CPUExecutionProvider"],
        preload_dlls=lambda: preloaded.append(True),
    ))
    assert common.inference_providers("cuda") == ["CUDAExecutionProvider", "CPUExecutionProvider"]
    assert common.inference_context_id("cuda") == 0
    assert preloaded == [True]


def test_cuda_requires_available_execution_provider(monkeypatch):
    monkeypatch.setitem(sys.modules, "onnxruntime", SimpleNamespace(
        get_available_providers=lambda: ["CPUExecutionProvider"],
    ))
    with pytest.raises(common.ToolError, match="CUDAExecutionProvider is unavailable"):
        common.inference_providers("cuda")


def test_face_analyzer_uses_cuda_for_detector_and_recognizer(monkeypatch):
    prepared = []
    loaded = []

    class FakeModel:
        detect = None
        get_feat = None

        def prepare(self, context_id, **kwargs):
            prepared.append(context_id)

    def fake_load(path, label, device="cpu"):
        loaded.append((label, device))
        return FakeModel()

    monkeypatch.setattr(detect_faces, "load_onnx_model", fake_load)
    detect_faces.FaceAnalyzer(device="cuda")

    assert loaded == [("face detector", "cuda"), ("face recognizer", "cuda")]
    assert prepared == [0, 0]