"""
LocalPublisher — supervise an embedded MediaMTX child that exposes Camera
Module 3 as ``rtsp://<host>:<port>/<cam_id>`` for the controller's MediaMTX
to pull and for the local recorder to consume over loopback.

Mirrors the supervisor shape used on the controller in
``controller/backend/app/mediamtx_manager.py``: a single subprocess owned
by this module, debounced restarts on configuration changes, gracefully
dormant when the ``mediamtx`` binary is not installed.

Concurrency:

- All state mutations are guarded by a single ``threading.RLock``.
- Lifecycle is owned by a single ``LocalPublisher`` instance per process
  (the FastAPI ``lifespan`` constructs it). The instance owns the
  ``subprocess.Popen`` and the debounce ``threading.Timer``.
- Reentrant: ``start()``/``stop()`` may be called multiple times safely.

Security:

- ``subprocess.Popen`` always uses ``shell=False`` with an explicit ``argv``
  list and ``stdin=DEVNULL``.
- Only the values listed in :data:`PRESETS` and the camera-id sanitiser are
  written into the YAML; everything else flowing in from
  :class:`PublisherConfig` is type-checked and range-checked.
- The ``mediamtx`` binary path is resolved through
  ``SURVEILLANCE_MEDIAMTX_BIN`` (must be a regular file) or ``shutil.which``
  on ``PATH``; we never invoke an arbitrary string given by the operator.
- No secrets are logged; failures from the child do not leak environment
  variables.

Input validation:

- Every public function validates its arguments and raises ``ValueError``
  on bad input. Env values pass through :func:`_int_env` and
  :func:`_str_env` which apply the same checks.
- Configuration changes that produce an identical YAML are dropped (no
  spurious restarts).
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import socket
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

# Default file used when we need to materialise the YAML for the child.
_CONFIG_FILENAME = "mediamtx.local_publisher.yml"

# Debounce window for restart-on-settings-change. Matches the controller-side
# supervisor so operator expectations are consistent across both Pis.
_RESTART_DEBOUNCE_SEC = 1.2

# Allowed ranges for input validation.
_PORT_MIN = 1024
_PORT_MAX = 65535

# Allowed quality knobs (also accepted from the controller settings).
_QUALITY_VALUES = ("low", "medium", "high")


@dataclass(frozen=True)
class _Preset:
    """Encoder/source preset bound to the operator's ``quality`` setting."""

    width: int
    height: int
    fps: int
    bitrate: int  # bits per second


PRESETS: dict[str, _Preset] = {
    "low": _Preset(width=640, height=480, fps=15, bitrate=800_000),
    "medium": _Preset(width=1280, height=720, fps=25, bitrate=2_500_000),
    "high": _Preset(width=1920, height=1080, fps=25, bitrate=4_000_000),
}


# Camera id sanitiser for the MediaMTX path key. MediaMTX path names accept a
# narrow alphabet; we normalise to ``[A-Za-z0-9_-]`` and fall back to
# ``camera1`` so a misconfigured operator never produces invalid YAML.
_PATH_KEY_RE = re.compile(r"[^A-Za-z0-9_-]+")


def _safe_path_key(cam_id: str) -> str:
    """
    Return a MediaMTX-safe path segment derived from ``cam_id``.

    Reentrant; pure function.
    """
    if not isinstance(cam_id, str):
        raise ValueError("cam_id must be a string")
    cleaned = _PATH_KEY_RE.sub("_", cam_id.strip()).strip("_")
    return cleaned or "camera1"


def _validate_quality(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("quality must be a string")
    v = value.strip().lower()
    if v not in _QUALITY_VALUES:
        raise ValueError(
            f"quality must be one of {_QUALITY_VALUES!r}, got {value!r}"
        )
    return v


def _validate_port(value: Any, *, name: str) -> int:
    try:
        p = int(value)
    except (TypeError, ValueError) as e:
        raise ValueError(f"{name} must be an integer") from e
    if p < _PORT_MIN or p > _PORT_MAX:
        raise ValueError(
            f"{name} must be in [{_PORT_MIN}, {_PORT_MAX}], got {p}"
        )
    return p


def _str_env(name: str, default: str = "") -> str:
    if not isinstance(name, str) or not name:
        raise ValueError("env name must be a non-empty string")
    v = os.environ.get(name)
    return default if v is None else str(v)


def _int_env(name: str, default: int) -> int:
    raw = _str_env(name, "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except ValueError as e:
        raise ValueError(f"{name} must be an integer, got {raw!r}") from e


def _bool_env(name: str) -> bool:
    raw = _str_env(name, "").strip().lower()
    return raw in ("1", "true", "yes", "on")


@dataclass
class PublisherConfig:
    """
    Validated configuration for a :class:`LocalPublisher` instance.

    Built from the process environment and the recorder's current settings.
    Frozen at instantiation; the supervisor takes a fresh ``PublisherConfig``
    every time it regenerates YAML, so two configs are equal iff their
    serialised YAML would be identical.
    """

    enabled: bool
    cam_id: str
    bind_port: int
    quality: str
    flip_180: bool
    bin_path: Optional[str] = None
    config_dir: Path = field(default_factory=lambda: Path("."))
    lan_ip: str = "127.0.0.1"

    def __post_init__(self) -> None:
        if not isinstance(self.cam_id, str) or not self.cam_id.strip():
            raise ValueError("cam_id must be a non-empty string")
        self.cam_id = _safe_path_key(self.cam_id)
        self.bind_port = _validate_port(self.bind_port, name="bind_port")
        self.quality = _validate_quality(self.quality)
        if not isinstance(self.flip_180, bool):
            raise ValueError("flip_180 must be a bool")
        if self.bin_path is not None and not isinstance(self.bin_path, str):
            raise ValueError("bin_path must be a string or None")
        if not isinstance(self.config_dir, Path):
            raise ValueError("config_dir must be a Path")
        if not isinstance(self.lan_ip, str) or not self.lan_ip:
            raise ValueError("lan_ip must be a non-empty string")

    def loopback_url(self) -> str:
        """RTSP URL for in-process consumers (the recorder)."""
        return f"rtsp://127.0.0.1:{self.bind_port}/{self.cam_id}"

    def lan_url(self) -> str:
        """RTSP URL advertised to the controller via mDNS."""
        return f"rtsp://{self.lan_ip}:{self.bind_port}/{self.cam_id}"


def resolve_mediamtx_binary(env_override: Optional[str] = None) -> Optional[str]:
    """
    Resolve the ``mediamtx`` executable. Returns an absolute path or ``None``.

    Precedence:

    1. ``env_override`` (or ``$SURVEILLANCE_MEDIAMTX_BIN``) — must be a regular
       file and resolvable to an absolute path.
    2. ``shutil.which("mediamtx")`` on ``PATH``.

    Reentrant; pure (only reads env / filesystem).
    """
    candidates: list[str] = []
    explicit = (
        env_override
        if env_override is not None
        else _str_env("SURVEILLANCE_MEDIAMTX_BIN", "")
    )
    explicit = explicit.strip()
    if explicit:
        candidates.append(explicit)
    candidates.append("mediamtx")
    for c in candidates:
        try:
            p = Path(c)
            if p.is_file():
                return str(p.resolve())
        except OSError:
            pass
        w = shutil.which(c)
        if w:
            return w
    return None


def _guess_lan_ip(target: str = "192.168.2.139") -> str:
    """
    Return the local IPv4 address used to reach ``target``.

    Falls back to ``127.0.0.1`` if the kernel cannot determine a route. The
    forced override env ``SURVEILLANCE_EDGE_IP`` short-circuits this.
    """
    forced = _str_env("SURVEILLANCE_EDGE_IP", "").strip()
    if forced:
        return forced
    if not isinstance(target, str) or not target.strip():
        target = "192.168.2.139"
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect((target, 1))
        ip = s.getsockname()[0]
        if ip and ip != "0.0.0.0":
            return str(ip)
    except OSError:
        pass
    finally:
        s.close()
    return "127.0.0.1"


def build_config_from_env(
    *,
    quality: str = "medium",
    flip_180: bool = False,
    config_dir: Optional[Path] = None,
) -> PublisherConfig:
    """
    Derive a :class:`PublisherConfig` from the process environment plus the
    runtime ``quality`` / ``flip_180`` knobs that come from the recorder.

    Validation is centralised here so :class:`LocalPublisher` itself never
    has to interpret env strings.
    """
    enabled = _bool_env("SURVEILLANCE_PI_CAMERA")
    cam_id = _str_env("SURVEILLANCE_EDGE_CAMERA_ID", "camera1").strip() or "camera1"
    # An explicit publisher path overrides the camera id (matches the existing
    # mDNS field used by zeroconf_publish).
    advertised_path = _str_env("SURVEILLANCE_MEDIAMTX_PATH", "").strip()
    if advertised_path:
        cam_id = advertised_path
    bind_port = _int_env("SURVEILLANCE_PUBLISHER_PORT", 8554)
    bin_path = resolve_mediamtx_binary()
    target = _str_env("SURVEILLANCE_CONTROLLER_IP", "192.168.2.139").strip() or "192.168.2.139"
    lan_ip = _guess_lan_ip(target)
    if config_dir is None:
        # Default next to the agent so it's easy to inspect post-mortem.
        edge_root = Path(__file__).resolve().parent.parent
        config_dir = edge_root / "data"
    return PublisherConfig(
        enabled=enabled,
        cam_id=cam_id,
        bind_port=bind_port,
        quality=quality,
        flip_180=flip_180,
        bin_path=bin_path,
        config_dir=config_dir,
        lan_ip=lan_ip,
    )


def build_yaml(cfg: PublisherConfig) -> str:
    """
    Render the MediaMTX YAML for a single rpiCamera path.

    Pure function; no I/O. The output is deterministic given equal inputs,
    which lets the supervisor avoid restarting on no-op changes.
    """
    if not isinstance(cfg, PublisherConfig):
        raise ValueError("cfg must be a PublisherConfig")
    preset = PRESETS[cfg.quality]
    path_key = _safe_path_key(cfg.cam_id)
    flip_str = "yes" if cfg.flip_180 else "no"
    # ``json.dumps`` quotes the bind address safely (handles colons, etc).
    rtsp_addr = json.dumps(f":{cfg.bind_port}")
    lines = [
        "# Generated by SmartCam edge-agent LocalPublisher.",
        "# Overwritten on every settings change; do not hand-edit.",
        "logLevel: warn",
        "logDestinations: [stdout]",
        "api: no",
        "webrtc: no",
        "hls: no",
        "rtmp: no",
        "srt: no",
        "rtsp: yes",
        f"rtspAddress: {rtsp_addr}",
        "rtspTransports: [tcp, udp]",
        "paths:",
        f"  {path_key}:",
        "    source: rpiCamera",
        f"    rpiCameraWidth: {preset.width}",
        f"    rpiCameraHeight: {preset.height}",
        f"    rpiCameraFPS: {preset.fps}",
        f"    rpiCameraBitrate: {preset.bitrate}",
        f"    rpiCameraHFlip: {flip_str}",
        f"    rpiCameraVFlip: {flip_str}",
    ]
    return "\n".join(lines) + "\n"


class LocalPublisher:
    """
    Owns a single ``mediamtx`` child process for the local Pi camera path.

    Lifecycle:

    - :py:meth:`start` is idempotent. If the publisher is disabled, the
      ``mediamtx`` binary is missing, or a previous start failed, it logs
      once and returns without raising — the rest of the agent must continue
      to run regardless.
    - :py:meth:`update_settings` is the only way to change ``quality`` or
      ``flip_180`` after start; it never blocks on the actual restart
      because the work is debounced onto a background timer.
    - :py:meth:`stop` is idempotent and joins the debounce timer, then
      terminates the child with SIGTERM (kills after 8 s if it ignores).

    Concurrency / ownership:

    - All state mutations happen under ``self._lock`` (a ``threading.RLock``).
    - The child process is owned by this instance; ``stop`` is called from
      the FastAPI ``lifespan``'s ``finally`` block to guarantee release.
    - The instance can be replaced (e.g. on hot-reload), but two live
      instances must not share the same bind port; the second one will
      simply fail to bind and ``running`` will become ``False``.

    The class is safe to call across threads and intentionally never invokes
    blocking I/O while holding ``self._lock`` apart from ``Popen`` startup
    and ``Popen.wait`` during shutdown — both of which are bounded.
    """

    def __init__(
        self,
        *,
        recorder_settings_provider: Optional[Callable[[], dict[str, Any]]] = None,
        config_dir: Optional[Path] = None,
    ) -> None:
        if recorder_settings_provider is not None and not callable(
            recorder_settings_provider
        ):
            raise ValueError("recorder_settings_provider must be callable")
        if config_dir is not None and not isinstance(config_dir, Path):
            raise ValueError("config_dir must be a Path")
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen[bytes]] = None
        self._cfg: Optional[PublisherConfig] = None
        self._last_yaml: str = ""
        self._debounce_timer: Optional[threading.Timer] = None
        self._stopped = False
        self._provider = recorder_settings_provider
        self._explicit_config_dir = config_dir
        # Whether the operator has been informed once that the binary is
        # missing — avoids spamming the log on every restart attempt.
        self._missing_logged = False

    # ----------------------------- helpers -----------------------------

    def _current_settings(self) -> dict[str, Any]:
        if self._provider is None:
            return {}
        try:
            data = self._provider()
        except Exception as e:
            logger.warning("LocalPublisher settings provider failed: %s", e)
            return {}
        return data if isinstance(data, dict) else {}

    def _build_cfg(self) -> PublisherConfig:
        s = self._current_settings()
        quality = str(s.get("quality", "medium") or "medium").lower()
        if quality not in _QUALITY_VALUES:
            quality = "medium"
        flip = bool(s.get("flip_180", False))
        return build_config_from_env(
            quality=quality,
            flip_180=flip,
            config_dir=self._explicit_config_dir,
        )

    def _config_path(self, cfg: PublisherConfig) -> Path:
        cfg.config_dir.mkdir(parents=True, exist_ok=True)
        return cfg.config_dir / _CONFIG_FILENAME

    def _terminate_proc(self, *, fast: bool = False) -> None:
        proc = self._proc
        self._proc = None
        if proc is None:
            return
        grace = 1.0 if fast else 4.0
        try:
            if proc.poll() is not None:
                return
            proc.terminate()
            proc.wait(timeout=grace)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except Exception as e:
                logger.warning("LocalPublisher kill: %s", e)
        except Exception as e:
            logger.warning("LocalPublisher terminate: %s", e)

    def _spawn(self, bin_path: str, config_path: Path) -> Optional[subprocess.Popen[bytes]]:
        env = {"PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin")}
        try:
            return subprocess.Popen(
                [bin_path, str(config_path)],
                stdin=subprocess.DEVNULL,
                cwd=str(config_path.parent),
                env=env,
            )
        except OSError as e:
            logger.error("LocalPublisher: failed to spawn %s: %s", bin_path, e)
            return None

    # ------------------------------ API --------------------------------

    @property
    def running(self) -> bool:
        with self._lock:
            return self._proc is not None and self._proc.poll() is None

    def snapshot(self) -> dict[str, Any]:
        """Return a picklable, immutable view of the publisher state."""
        with self._lock:
            cfg = self._cfg
            running = self._proc is not None and self._proc.poll() is None
            return {
                "enabled": bool(cfg.enabled) if cfg else False,
                "running": running,
                "binary": cfg.bin_path if cfg else None,
                "loopback_url": cfg.loopback_url()
                if cfg and cfg.enabled and running
                else None,
                "lan_url": cfg.lan_url() if cfg and cfg.enabled and running else None,
                "cam_id": cfg.cam_id if cfg else None,
                "quality": cfg.quality if cfg else None,
                "flip_180": cfg.flip_180 if cfg else None,
                "bind_port": cfg.bind_port if cfg else None,
            }

    def effective_rtsp_url(self) -> Optional[str]:
        """
        URL the recorder should consume.

        Returns ``None`` when the publisher is disabled or its child is not
        running, so the caller (the lifespan) can keep operator-supplied
        ``SURVEILLANCE_RTSP_URL`` precedence intact.
        """
        with self._lock:
            cfg = self._cfg
            if not cfg or not cfg.enabled:
                return None
            if self._proc is None or self._proc.poll() is not None:
                return None
            return cfg.loopback_url()

    def advertised_rtsp_url(self) -> Optional[str]:
        """
        URL the agent should advertise over mDNS for the controller to pull.
        """
        with self._lock:
            cfg = self._cfg
            if not cfg or not cfg.enabled:
                return None
            if self._proc is None or self._proc.poll() is not None:
                return None
            return cfg.lan_url()

    def start(self) -> None:
        """Start (or restart, idempotent) the supervisor."""
        with self._lock:
            self._stopped = False
        try:
            cfg = self._build_cfg()
        except ValueError as e:
            logger.error("LocalPublisher disabled: invalid config: %s", e)
            with self._lock:
                self._cfg = None
            return
        with self._lock:
            self._cfg = cfg
        if not cfg.enabled:
            logger.info(
                "LocalPublisher disabled (set SURVEILLANCE_PI_CAMERA=1 to enable)"
            )
            return
        if not cfg.bin_path:
            if not self._missing_logged:
                logger.warning(
                    "LocalPublisher: mediamtx binary not found "
                    "(set SURVEILLANCE_MEDIAMTX_BIN or install via "
                    "edge-agent/scripts/install-rpi-mediamtx.sh)"
                )
                self._missing_logged = True
            return
        self._missing_logged = False
        self._apply()

    def stop(self, *, fast: bool = True) -> None:
        """Stop the supervisor and join the debounce timer. ``fast=True`` for Ctrl+C."""
        with self._lock:
            self._stopped = True
            timer = self._debounce_timer
            self._debounce_timer = None
        if timer is not None:
            try:
                timer.cancel()
                timer.join(timeout=0.5 if fast else 2.0)
            except Exception as e:
                logger.debug("LocalPublisher timer cancel: %s", e)
        with self._lock:
            self._terminate_proc(fast=fast)
            self._last_yaml = ""

    def update_settings(self, settings: dict[str, Any]) -> None:
        """
        Notify the supervisor of new recorder settings.

        Only ``flip_180`` and ``quality`` are honoured; other keys are
        ignored. Schedules a debounced restart if the resulting YAML
        differs from the running one.

        Reentrant; safe to call from any thread.
        """
        if not isinstance(settings, dict):
            raise ValueError("settings must be a dict")
        with self._lock:
            if self._stopped:
                return
            cfg = self._cfg
            if cfg is None or not cfg.enabled:
                return
            quality = settings.get("quality", cfg.quality)
            flip = bool(settings.get("flip_180", cfg.flip_180))
            try:
                quality = _validate_quality(quality)
            except ValueError:
                quality = cfg.quality
            new_cfg = PublisherConfig(
                enabled=cfg.enabled,
                cam_id=cfg.cam_id,
                bind_port=cfg.bind_port,
                quality=quality,
                flip_180=flip,
                bin_path=cfg.bin_path,
                config_dir=cfg.config_dir,
                lan_ip=cfg.lan_ip,
            )
            self._cfg = new_cfg
            self._schedule_apply()

    def _schedule_apply(self) -> None:
        """Debounce; called with ``self._lock`` already held."""
        if self._debounce_timer is not None:
            try:
                self._debounce_timer.cancel()
            except Exception:
                pass
            self._debounce_timer = None
        t = threading.Timer(_RESTART_DEBOUNCE_SEC, self._apply_safe)
        t.daemon = True
        self._debounce_timer = t
        t.start()

    def _apply_safe(self) -> None:
        try:
            self._apply()
        except Exception:
            logger.exception("LocalPublisher debounced apply failed")

    def _apply(self) -> None:
        """
        Render YAML for the current ``self._cfg`` and (re)spawn ``mediamtx``
        if the YAML changed or the child is no longer running.
        """
        with self._lock:
            if self._stopped:
                return
            cfg = self._cfg
            if cfg is None or not cfg.enabled or not cfg.bin_path:
                return
            yaml_text = build_yaml(cfg)
            running = self._proc is not None and self._proc.poll() is None
            if running and yaml_text == self._last_yaml:
                return
            cfg_path = self._config_path(cfg)
            try:
                cfg_path.write_text(yaml_text, encoding="utf-8")
            except OSError as e:
                logger.error("LocalPublisher: cannot write config: %s", e)
                return
            self._terminate_proc()
            proc = self._spawn(cfg.bin_path, cfg_path)
            if proc is None:
                self._last_yaml = ""
                return
            self._proc = proc
            self._last_yaml = yaml_text
            logger.info(
                "LocalPublisher: mediamtx pid=%s bind=:%s path=%s quality=%s flip=%s",
                proc.pid,
                cfg.bind_port,
                cfg.cam_id,
                cfg.quality,
                cfg.flip_180,
            )
            # Give the child a moment to bind so the recorder doesn't race it.
            # Bounded wait — never blocks longer than 1 s total.
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                if self._proc is None or self._proc.poll() is not None:
                    break
                time.sleep(0.05)
