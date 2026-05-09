"""
Backward-compat smoke check for the edge ``/health`` endpoint.

Imports the FastAPI app and validates the response shape **without**
launching a real recorder or MediaMTX child. Heavy dependencies (cv2,
MQTT, zeroconf) are skipped if unavailable so the test is portable to a
laptop CI runner.
"""

from __future__ import annotations

import importlib

import pytest

# These dependencies are only available on the edge host. Skip the whole
# module if any of them is missing — the test is a smoke check, not a
# full integration suite.
pytest.importorskip("fastapi")
pytest.importorskip("cv2")
pytest.importorskip("paho.mqtt.client")
pytest.importorskip("zeroconf")

from fastapi.testclient import TestClient  # noqa: E402

EXPECTED_KEYS = {
    "role",
    "camera_mqtt_id",
    "controller_pi5_mqtt_host",
    "rtsp_configured",
    "rtsp_source",
    "model_dir",
    "model_files_present",
    "recordings_dir",
    "mdns_service",
    "edge_http_port",
    "edge_display_name",
    "mediamtx_path",
    "publisher_enabled",
    "publisher_running",
    "publisher_url",
    "publisher_lan_url",
    "mediamtx_binary",
}


def test_health_shape_when_disabled(monkeypatch):
    monkeypatch.delenv("SURVEILLANCE_PI_CAMERA", raising=False)
    monkeypatch.delenv("SURVEILLANCE_RTSP_URL", raising=False)
    main = importlib.import_module("app.main")
    importlib.reload(main)
    body = main.health()
    assert set(body.keys()) >= EXPECTED_KEYS
    assert body["role"] == "edge"
    assert body["rtsp_configured"] is False
    assert body["rtsp_source"] in ("none", "operator", "publisher")
    # Module-scope helpers should not crash when called pre-lifespan.
    assert body["publisher_enabled"] is False
    assert body["publisher_running"] is False
