import os
import platform

from src import environment


def test_check_machine_supports_windows(monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Windows")
    monkeypatch.setattr(os, "cpu_count", lambda: 16)
    monkeypatch.setattr(environment, "_read_windows_cpu_name", lambda: "Intel(R) Core(TM) i7-11700K")
    monkeypatch.setattr(environment, "_read_windows_ram_gb", lambda: 15.5)

    checks = environment._check_machine()

    assert any(c.name == "CPU" and "Intel(R) Core(TM) i7-11700K" in c.detail for c in checks)
    assert any(c.name == "Available RAM" and "15.5 GB" in c.detail for c in checks)
