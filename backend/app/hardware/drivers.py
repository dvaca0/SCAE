"""Hardware abstraction for cameras and barrier controllers.

Nothing in SCAE talks to a device directly. Everything goes through these
two protocols, and a driver is selected per-device by the `driver` string
stored on the Camera / BarrierController row.

That means adding UniFi Protect, an RTSP stream or a Raspberry Pi relay is:
   1. write a class here,
   2. register it in the registry below,
   3. set `driver` + `config` on the device row.

No schema change, no change to the decision engine, no change to the API.
Today only the mock drivers are implemented; the RTSP and HTTP-relay ones
are declared with their config contract so the shape is fixed and honest
about not being wired yet.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


class DriverNotAvailable(RuntimeError):
    """Raised when a driver exists in the registry but cannot run here."""


@dataclass
class CaptureResult:
    image_bytes: Optional[bytes]
    content_type: str = "image/jpeg"
    captured_at: datetime = None  # type: ignore[assignment]
    detail: str = ""

    def __post_init__(self):
        if self.captured_at is None:
            self.captured_at = datetime.now(timezone.utc)


@dataclass
class ActuationResult:
    succeeded: bool
    simulated: bool
    detail: str


@runtime_checkable
class CameraDriver(Protocol):
    kind: str

    def capture(self, config: dict[str, Any]) -> CaptureResult: ...
    def health(self, config: dict[str, Any]) -> tuple[bool, str]: ...


@runtime_checkable
class BarrierDriver(Protocol):
    kind: str

    def open(self, config: dict[str, Any], reason: str) -> ActuationResult: ...
    def health(self, config: dict[str, Any]) -> tuple[bool, str]: ...


# --------------------------------------------------------------------------
# Camera drivers
# --------------------------------------------------------------------------
class MockCameraDriver:
    """Development camera. Reports healthy, captures nothing.

    Frames arrive through the guard console upload instead, which is how
    the system is operated until real cameras exist.
    """

    kind = "mock"
    requires_config: tuple[str, ...] = ()

    def capture(self, config: dict[str, Any]) -> CaptureResult:
        return CaptureResult(
            image_bytes=None,
            detail="Cámara simulada: la captura llega desde la consola del guardia.",
        )

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return True, "Simulada"


class FileCameraDriver:
    """Reads frames from a local image or video file.

    Useful for replaying recorded gate footage during development. This is
    where the prototype's `camera.py` generator belongs.
    """

    kind = "file"
    requires_config = ("path",)

    def capture(self, config: dict[str, Any]) -> CaptureResult:
        path = config.get("path")
        if not path:
            raise DriverNotAvailable("El driver 'file' requiere config.path")
        try:
            import cv2

            capture = cv2.VideoCapture(path)
            ok, frame = capture.read()
            capture.release()
            if not ok:
                return CaptureResult(None, detail=f"No se pudo leer {path}")
            ok, buf = cv2.imencode(".jpg", frame)
            return CaptureResult(buf.tobytes() if ok else None, detail=f"Frame de {path}")
        except ImportError as exc:  # pragma: no cover - depends on host
            raise DriverNotAvailable("OpenCV no disponible") from exc

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        from pathlib import Path

        path = config.get("path")
        if path and Path(path).exists():
            return True, f"Archivo disponible: {path}"
        return False, f"Archivo no encontrado: {path}"


class RtspCameraDriver:
    """Generic IP camera over RTSP. Contract fixed, not yet wired.

    config: {"url": "rtsp://user:pass@host:554/stream", "timeout_s": 5}
    """

    kind = "rtsp"
    requires_config = ("url",)

    def capture(self, config: dict[str, Any]) -> CaptureResult:
        raise DriverNotAvailable(
            "El driver RTSP aún no está implementado. Requiere una cámara IP "
            "accesible y validación de latencia en sitio."
        )

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return False, "Driver RTSP declarado pero no implementado."


class UnifiProtectCameraDriver:
    """UniFi Protect. Contract fixed, not yet wired.

    config: {"host": "...", "camera_id": "...", "api_key_ref": "..."}
    Credentials are referenced, never stored inline in the device row.
    """

    kind = "unifi_protect"
    requires_config = ("host", "camera_id")

    def capture(self, config: dict[str, Any]) -> CaptureResult:
        raise DriverNotAvailable(
            "El driver UniFi Protect aún no está implementado. Requiere "
            "credenciales del NVR y acceso a la red institucional."
        )

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return False, "Driver UniFi Protect declarado pero no implementado."


# --------------------------------------------------------------------------
# Barrier drivers
# --------------------------------------------------------------------------
class MockBarrierDriver:
    """Simulates the barrier. Every actuation is recorded as SIMULATED so
    no report can mistake a simulation for a real gate opening."""

    kind = "mock"
    requires_config: tuple[str, ...] = ()

    def open(self, config: dict[str, Any], reason: str) -> ActuationResult:
        logger.info("Barrera simulada abierta (%s)", reason)
        return ActuationResult(True, simulated=True, detail=f"Apertura simulada: {reason}")

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return True, "Simulado"


class HttpRelayBarrierDriver:
    """Relay board / Raspberry Pi exposing an HTTP endpoint.

    config: {"url": "http://pi.local/relay/1/pulse", "method": "POST",
             "pulse_ms": 800, "token_ref": "..."}
    """

    kind = "http_relay"
    requires_config = ("url",)

    def open(self, config: dict[str, Any], reason: str) -> ActuationResult:
        raise DriverNotAvailable(
            "El driver de relé HTTP aún no está implementado. Requiere el "
            "controlador físico y una prueba de seguridad de la barrera."
        )

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return False, "Driver de relé HTTP declarado pero no implementado."


class GpioBarrierDriver:
    """Direct GPIO on a Raspberry Pi hosting the lane agent.

    config: {"pin": 17, "active_high": true, "pulse_ms": 800}
    """

    kind = "gpio"
    requires_config = ("pin",)

    def open(self, config: dict[str, Any], reason: str) -> ActuationResult:
        raise DriverNotAvailable(
            "El driver GPIO sólo puede ejecutarse en el agente de carril "
            "(Raspberry Pi), no en el servidor central."
        )

    def health(self, config: dict[str, Any]) -> tuple[bool, str]:
        return False, "Driver GPIO declarado pero no implementado."


CAMERA_DRIVERS: dict[str, CameraDriver] = {
    d.kind: d() for d in (
        MockCameraDriver, FileCameraDriver, RtspCameraDriver, UnifiProtectCameraDriver,
    )
}

BARRIER_DRIVERS: dict[str, BarrierDriver] = {
    d.kind: d() for d in (
        MockBarrierDriver, HttpRelayBarrierDriver, GpioBarrierDriver,
    )
}

# Which drivers can actually do their job on this deployment today.
IMPLEMENTED_CAMERA_DRIVERS = {"mock", "file"}
IMPLEMENTED_BARRIER_DRIVERS = {"mock"}


def get_camera_driver(kind: str) -> CameraDriver:
    driver = CAMERA_DRIVERS.get(kind)
    if driver is None:
        raise DriverNotAvailable(f"Driver de cámara desconocido: {kind}")
    return driver


def get_barrier_driver(kind: str) -> BarrierDriver:
    driver = BARRIER_DRIVERS.get(kind)
    if driver is None:
        raise DriverNotAvailable(f"Driver de barrera desconocido: {kind}")
    return driver


def driver_catalog() -> dict[str, list[dict[str, Any]]]:
    """Describes every driver to the UI, including whether it is usable."""
    return {
        "cameras": [
            {
                "kind": kind,
                "implemented": kind in IMPLEMENTED_CAMERA_DRIVERS,
                "requires_config": list(getattr(drv, "requires_config", ())),
            }
            for kind, drv in CAMERA_DRIVERS.items()
        ],
        "barriers": [
            {
                "kind": kind,
                "implemented": kind in IMPLEMENTED_BARRIER_DRIVERS,
                "requires_config": list(getattr(drv, "requires_config", ())),
            }
            for kind, drv in BARRIER_DRIVERS.items()
        ],
    }
