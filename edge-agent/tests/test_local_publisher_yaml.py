"""
Pure-function tests for :mod:`app.local_publisher` YAML emission.

These tests do not spawn any subprocess. They only exercise the YAML
generator so the rest of the supervisor can rely on a deterministic config
hash for the no-op-restart skip path.

Concurrency: tests are independent; no shared state. Each test builds its
own :class:`PublisherConfig` and inspects the resulting string.

Security: no secrets used; no external resources accessed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from app.local_publisher import (
    PRESETS,
    PublisherConfig,
    _safe_path_key,
    build_yaml,
)


def _make_cfg(**overrides):
    base = dict(
        enabled=True,
        cam_id="camera1",
        bind_port=8554,
        quality="medium",
        flip_180=False,
        bin_path="/usr/local/bin/mediamtx",
        config_dir=Path("."),
        lan_ip="192.168.2.164",
    )
    base.update(overrides)
    return PublisherConfig(**base)


def test_yaml_contains_required_keys_and_path():
    cfg = _make_cfg(cam_id="frontdoor", quality="medium", flip_180=False)
    yaml = build_yaml(cfg)
    assert "rtsp: yes" in yaml
    assert 'rtspAddress: ":8554"' in yaml
    assert "paths:" in yaml
    assert "  frontdoor:" in yaml
    assert "    source: rpiCamera" in yaml
    p = PRESETS["medium"]
    assert f"rpiCameraWidth: {p.width}" in yaml
    assert f"rpiCameraHeight: {p.height}" in yaml
    assert f"rpiCameraFPS: {p.fps}" in yaml
    assert f"rpiCameraBitrate: {p.bitrate}" in yaml
    assert "rpiCameraHFlip: no" in yaml
    assert "rpiCameraVFlip: no" in yaml


def test_yaml_omits_version_fragile_webrtc_fields():
    """``webrtcAllowOrigin*`` is a moving target across MediaMTX releases.

    We use RTSP-only so there must be no webrtc allow-list field at all.
    """
    cfg = _make_cfg()
    yaml = build_yaml(cfg)
    assert "webrtc: no" in yaml
    assert "webrtcAllowOrigin" not in yaml
    assert "webrtcAllowOrigins" not in yaml


def test_yaml_quality_presets_round_trip():
    for q, preset in PRESETS.items():
        cfg = _make_cfg(quality=q)
        yaml = build_yaml(cfg)
        assert f"rpiCameraWidth: {preset.width}" in yaml
        assert f"rpiCameraHeight: {preset.height}" in yaml
        assert f"rpiCameraFPS: {preset.fps}" in yaml
        assert f"rpiCameraBitrate: {preset.bitrate}" in yaml


def test_yaml_flip_propagates_to_both_axes():
    cfg = _make_cfg(flip_180=True)
    yaml = build_yaml(cfg)
    assert "rpiCameraHFlip: yes" in yaml
    assert "rpiCameraVFlip: yes" in yaml


def test_yaml_is_deterministic_for_equal_config():
    a = build_yaml(_make_cfg())
    b = build_yaml(_make_cfg())
    assert a == b


def test_yaml_changes_when_config_changes():
    a = build_yaml(_make_cfg(quality="medium"))
    b = build_yaml(_make_cfg(quality="high"))
    assert a != b


def test_safe_path_key_normalises_unsafe_input():
    assert _safe_path_key("camera1") == "camera1"
    assert _safe_path_key("front door / 1") == "front_door_1"
    # Empty / whitespace / all-bad input falls back to a stable default.
    assert _safe_path_key("   ") == "camera1"
    assert _safe_path_key("///!@#") == "camera1"


def test_safe_path_key_rejects_non_string():
    with pytest.raises(ValueError):
        _safe_path_key(None)  # type: ignore[arg-type]


def test_publisher_config_validates_inputs():
    with pytest.raises(ValueError):
        _make_cfg(cam_id="")
    with pytest.raises(ValueError):
        _make_cfg(bind_port=0)
    with pytest.raises(ValueError):
        _make_cfg(bind_port=70000)
    with pytest.raises(ValueError):
        _make_cfg(quality="ultra")
    with pytest.raises(ValueError):
        _make_cfg(flip_180="yes")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        _make_cfg(lan_ip="")


def test_loopback_and_lan_urls():
    cfg = _make_cfg(cam_id="cam-A", bind_port=8554, lan_ip="10.0.0.42")
    assert cfg.loopback_url() == "rtsp://127.0.0.1:8554/cam-A"
    assert cfg.lan_url() == "rtsp://10.0.0.42:8554/cam-A"


def test_build_yaml_rejects_non_config():
    with pytest.raises(ValueError):
        build_yaml({"cam_id": "x"})  # type: ignore[arg-type]
