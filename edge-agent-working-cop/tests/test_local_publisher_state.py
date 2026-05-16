"""
Lifecycle / state-machine tests for :class:`app.local_publisher.LocalPublisher`.

These tests never spawn ``mediamtx``. ``subprocess.Popen`` and the binary
resolver are monkey-patched. They cover:

- start/stop is idempotent.
- start refuses to spawn when the binary is missing or the publisher is
  disabled (no exception escapes).
- ``update_settings`` debounces and respawns only when YAML actually
  changes.
- ``stop`` always terminates the running child and joins the timer.

Concurrency: each test resets module state via fresh fixtures and asserts
on the supervisor instance only. No shared globals are mutated.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import Any, Optional

import pytest

from app import local_publisher as lp


class _FakeProc:
    """Minimal stand-in for ``subprocess.Popen``.

    Tracks PID, terminate/kill calls, and exposes ``poll()`` semantics.
    """

    _pid_counter = 0

    def __init__(self) -> None:
        type(self)._pid_counter += 1
        self.pid = type(self)._pid_counter
        self.terminated = False
        self.killed = False
        self._returncode: Optional[int] = None

    def poll(self) -> Optional[int]:
        return self._returncode

    def terminate(self) -> None:
        self.terminated = True
        self._returncode = 0

    def kill(self) -> None:
        self.killed = True
        self._returncode = -9

    def wait(self, timeout: Optional[float] = None) -> int:
        if self._returncode is None:
            self._returncode = 0
        return self._returncode


@pytest.fixture
def env_pi_camera(monkeypatch):
    """Defaults that make the publisher 'enabled with binary present'."""
    monkeypatch.setenv("SURVEILLANCE_PI_CAMERA", "1")
    monkeypatch.setenv("SURVEILLANCE_EDGE_CAMERA_ID", "cam1")
    monkeypatch.delenv("SURVEILLANCE_MEDIAMTX_PATH", raising=False)
    monkeypatch.setenv("SURVEILLANCE_PUBLISHER_PORT", "8554")
    monkeypatch.setenv("SURVEILLANCE_EDGE_IP", "10.0.0.10")
    # Pretend the binary exists.
    monkeypatch.setattr(
        lp, "resolve_mediamtx_binary", lambda env_override=None: "/usr/local/bin/mediamtx"
    )


@pytest.fixture
def patched_popen(monkeypatch):
    spawned: list[_FakeProc] = []

    def fake_popen(*args: Any, **kwargs: Any) -> _FakeProc:
        proc = _FakeProc()
        spawned.append(proc)
        return proc

    monkeypatch.setattr(lp.subprocess, "Popen", fake_popen)
    return spawned


def _flush_debounce(pub: lp.LocalPublisher, *, timeout: float = 3.0) -> None:
    """Wait for the debounce ``threading.Timer`` to finish, if any."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with pub._lock:
            t = pub._debounce_timer
        if t is None or not t.is_alive():
            return
        time.sleep(0.05)


def test_start_disabled_is_noop(monkeypatch, tmp_path: Path):
    monkeypatch.delenv("SURVEILLANCE_PI_CAMERA", raising=False)
    pub = lp.LocalPublisher(
        recorder_settings_provider=lambda: {},
        config_dir=tmp_path,
    )
    pub.start()
    assert pub.running is False
    snap = pub.snapshot()
    assert snap["enabled"] is False
    assert snap["running"] is False


def test_start_without_binary_stays_dormant(monkeypatch, tmp_path: Path):
    monkeypatch.setenv("SURVEILLANCE_PI_CAMERA", "1")
    monkeypatch.setattr(
        lp, "resolve_mediamtx_binary", lambda env_override=None: None
    )
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    assert pub.running is False
    snap = pub.snapshot()
    assert snap["enabled"] is True
    assert snap["binary"] is None


def test_start_spawns_mediamtx(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    try:
        assert pub.running is True
        assert len(patched_popen) == 1
        # YAML config materialised on disk for the child.
        cfg_file = tmp_path / "mediamtx.local_publisher.yml"
        assert cfg_file.is_file()
        text = cfg_file.read_text()
        assert "source: rpiCamera" in text
        assert "  cam1:" in text
        snap = pub.snapshot()
        assert snap["enabled"] is True
        assert snap["running"] is True
        assert snap["loopback_url"] == "rtsp://127.0.0.1:8554/cam1"
        assert snap["lan_url"] == "rtsp://10.0.0.10:8554/cam1"
    finally:
        pub.stop()


def test_start_is_idempotent(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    pub.start()  # same YAML, same running child -> no respawn.
    try:
        assert len(patched_popen) == 1
    finally:
        pub.stop()


def test_update_settings_debounces_restart(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    try:
        # Trigger a change that alters YAML; debounce schedules an apply.
        pub.update_settings({"quality": "high"})
        _flush_debounce(pub)
        assert len(patched_popen) == 2  # one initial, one after debounced restart
        # First proc terminated cleanly.
        assert patched_popen[0].terminated is True
        # Second YAML reflects the new preset.
        cfg_file = tmp_path / "mediamtx.local_publisher.yml"
        assert "rpiCameraWidth: 1920" in cfg_file.read_text()
    finally:
        pub.stop()


def test_update_settings_no_change_no_restart(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    try:
        pub.update_settings({"quality": "medium", "flip_180": False})
        _flush_debounce(pub)
        # Same YAML, child still healthy -> no respawn.
        assert len(patched_popen) == 1
    finally:
        pub.stop()


def test_update_settings_invalid_quality_falls_back(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    try:
        pub.update_settings({"quality": "ultra-mega"})
        _flush_debounce(pub)
        # Falls back to the previously-active quality (medium) -> no respawn.
        assert len(patched_popen) == 1
    finally:
        pub.stop()


def test_stop_terminates_child_and_joins_timer(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    pub.update_settings({"flip_180": True})
    pub.stop()
    # Either the debounced restart already fired (2 procs) or stop cancelled it
    # before it ran (1 proc); in both cases nothing is left running.
    for proc in patched_popen:
        assert proc.terminated or proc.killed
    assert pub.running is False
    snap = pub.snapshot()
    assert snap["running"] is False
    # Idempotent stop.
    pub.stop()


def test_update_settings_validates_dict_type(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    try:
        with pytest.raises(ValueError):
            pub.update_settings("not-a-dict")  # type: ignore[arg-type]
    finally:
        pub.stop()


def test_effective_url_only_when_running(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    assert pub.effective_rtsp_url() is None
    pub.start()
    try:
        assert pub.effective_rtsp_url() == "rtsp://127.0.0.1:8554/cam1"
        assert pub.advertised_rtsp_url() == "rtsp://10.0.0.10:8554/cam1"
    finally:
        pub.stop()
    # After stop, effective URL is None again.
    assert pub.effective_rtsp_url() is None


def test_settings_provider_exception_is_swallowed(env_pi_camera, patched_popen, tmp_path: Path):
    def boom() -> dict[str, Any]:
        raise RuntimeError("snapshot failed")

    pub = lp.LocalPublisher(
        recorder_settings_provider=boom,
        config_dir=tmp_path,
    )
    pub.start()
    try:
        # Should still come up using the safe defaults from build_config_from_env.
        assert pub.running is True
    finally:
        pub.stop()


def test_concurrent_update_settings_is_thread_safe(env_pi_camera, patched_popen, tmp_path: Path):
    pub = lp.LocalPublisher(config_dir=tmp_path)
    pub.start()
    errors: list[BaseException] = []

    def hammer() -> None:
        try:
            for _ in range(50):
                pub.update_settings({"quality": "high"})
                pub.update_settings({"quality": "medium"})
        except BaseException as e:  # pragma: no cover - shouldn't happen
            errors.append(e)

    threads = [threading.Thread(target=hammer) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    _flush_debounce(pub, timeout=5.0)
    pub.stop()
    assert errors == []
