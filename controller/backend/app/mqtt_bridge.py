"""MQTT subscriber on controller: per-camera recording topics -> UI state + WebSocket fan-out."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
from typing import Any, Callable, Optional

import paho.mqtt.client as mqtt

from . import camera_store

logger = logging.getLogger(__name__)


def _resolve_controller_cam_id(topic_camera_id: str) -> Optional[int]:
    tid = topic_camera_id.strip()
    for c in camera_store.list_cameras():
        if camera_store.mqtt_id_for_camera(c) == tid:
            return int(c["id"])
    try:
        as_int = int(tid)
    except ValueError:
        return None
    if camera_store.get_camera(as_int):
        return as_int
    return None


class RecordingWsHub:
    """Thread-safe registration; broadcast from MQTT thread via run_coroutine_threadsafe."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._clients: list[Any] = []
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        self._loop = loop

    async def register(self, websocket: Any) -> None:
        with self._lock:
            self._clients.append(websocket)

    async def unregister(self, websocket: Any) -> None:
        with self._lock:
            if websocket in self._clients:
                self._clients.remove(websocket)

    def broadcast_json(self, payload: dict[str, Any]) -> None:
        loop = self._loop
        if loop is None:
            return

        async def _send_all() -> None:
            with self._lock:
                clients = list(self._clients)
            dead: list[Any] = []
            for ws in clients:
                try:
                    await ws.send_json(payload)
                except Exception:
                    dead.append(ws)
            if dead:
                with self._lock:
                    for ws in dead:
                        if ws in self._clients:
                            self._clients.remove(ws)

        try:
            asyncio.run_coroutine_threadsafe(_send_all(), loop)
        except RuntimeError:
            pass


class MqttRecordingBridge:
    """
    Subscribes to ``surveillance/cameras/{mqtt_id}/recording`` JSON payloads.
    """

    def __init__(
        self,
        host: str,
        port: int = 1883,
        *,
        username: Optional[str] = None,
        password: Optional[str] = None,
        topic_prefix: str = "surveillance/cameras",
    ) -> None:
        self._host = host.strip()
        self._port = int(port)
        self._username = username
        self._password = password
        self._prefix = topic_prefix.strip().rstrip("/")
        self._hub = RecordingWsHub()
        self._lock = threading.Lock()
        self._state: dict[int, dict[str, Any]] = {}
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._client: Optional[mqtt.Client] = None

    @property
    def ws_hub(self) -> RecordingWsHub:
        return self._hub

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            out: dict[str, Any] = {}
            for cid, row in self._state.items():
                out[str(cid)] = dict(row)
            return {"cameras": out}

    def _publish_snapshot(self) -> None:
        self._hub.broadcast_json(self.snapshot())

    def _on_message(self, _cli: Any, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        try:
            parts = msg.topic.split("/")
            if len(parts) < 4 or parts[-1] != "recording":
                return
            topic_cam = parts[2]
            cid = _resolve_controller_cam_id(topic_cam)
            if cid is None:
                return
            payload = json.loads(msg.payload.decode("utf-8"))
        except Exception as e:
            logger.debug("mqtt message skip: %s", e)
            return

        status = str(payload.get("status", "")).strip()
        recording = status in ("Start", "InProgress")
        row = {
            "recording": recording,
            "status": status,
            "recording_id": payload.get("recording_id"),
            "timestamp": payload.get("timestamp"),
            "objects_detected": payload.get("objects_detected"),
            "local_path": payload.get("local_path"),
        }
        with self._lock:
            if status == "Stop":
                self._state[cid] = {
                    "recording": False,
                    "status": "Stop",
                    "recording_id": payload.get("recording_id"),
                    "timestamp": payload.get("timestamp"),
                    "objects_detected": payload.get("objects_detected"),
                    "local_path": payload.get("local_path"),
                }
            else:
                self._state[cid] = row
        self._publish_snapshot()

    def _run(self) -> None:
        client = mqtt.Client()
        self._client = client
        client.on_message = self._on_message
        if self._username:
            client.username_pw_set(self._username, self._password or "")
        try:
            client.connect(self._host, self._port, keepalive=30)
            client.subscribe(f"{self._prefix}/+/recording", qos=0)
            client.loop_start()
            self._stop.wait()
            client.loop_stop()
        except Exception as e:
            logger.error("MQTT bridge error: %s", e)
        finally:
            try:
                client.disconnect()
            except Exception:
                pass

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="mqtt-bridge", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)


_bridge: Optional[MqttRecordingBridge] = None


def get_bridge() -> Optional[MqttRecordingBridge]:
    return _bridge


def init_bridge_from_env(ws_hub_loop: asyncio.AbstractEventLoop) -> Optional[MqttRecordingBridge]:
    global _bridge
    import os

    host = os.environ.get("CONTROLLER_MQTT_HOST", "").strip()
    if not host:
        _bridge = None
        return None
    port = int(os.environ.get("CONTROLLER_MQTT_PORT", "1883"))
    user = os.environ.get("CONTROLLER_MQTT_USER") or None
    pwd = os.environ.get("CONTROLLER_MQTT_PASSWORD") or None
    prefix = os.environ.get("CONTROLLER_MQTT_TOPIC_PREFIX", "surveillance/cameras").strip()
    _bridge = MqttRecordingBridge(host, port, username=user, password=pwd, topic_prefix=prefix)
    _bridge.ws_hub.set_loop(ws_hub_loop)
    _bridge.start()
    return _bridge


def shutdown_bridge() -> None:
    global _bridge
    if _bridge:
        _bridge.stop()
    _bridge = None
