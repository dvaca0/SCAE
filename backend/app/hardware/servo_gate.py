"""Servo real de la barrera, sobre una linea GPIO de la Raspberry Pi.

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

Este modulo **mueve hardware fisico**. Es el unico de SCAE que lo hace.

Contexto
--------
Hasta ahora todos los carriles usaban el driver `mock`, que registraba cada
apertura como SIMULATED para que ningun reporte confundiera una simulacion con
una barrera abriendose. El operador pidio explicitamente conectar el servo real
del carril del guardia (senal en GPIO17, negativo ya alimentado), asi que ahora
existe un driver que de verdad pulsa la linea. Los demas carriles siguen en
`mock`: esto se activa por fila de `barrier_controller`, no globalmente.

Por que se pulsa y se suelta, en vez de mantener la posicion
------------------------------------------------------------
La Pi 5 no tiene PWM hardware en GPIO17 (el PWM del SoC sale por 12/13/18/19),
asi que los pulsos los genera `lgpio.tx_servo`, que es software. Su propia
documentacion advierte: "I would only use software timed servo pulses for
testing purposes. The timing jitter will cause the servo to fidget. This may
cause it to overheat and wear out prematurely."

Mantener el tren de pulsos indefinidamente es exactamente ese fallo: un servo
temblando durante horas en una caseta. Asi que aqui se pulsa **solo mientras el
brazo viaja** (`travel_ms`) y despues se deja de pulsar. Sin pulsos el servo no
recibe consigna: ni tiembla ni se calienta. La posicion la sostiene la propia
mecanica del servo y el peso del brazo.

Consecuencia honesta: **no hay sensor de posicion**. Este driver sabe que
transmitio los pulsos, no que la pluma se haya movido. Por eso el detalle que
devuelve dice "pulsos transmitidos", nunca "barrera abierta".

Seguridad
---------
* `SCAE_GATE_ENABLED` tiene que ser true. En false el driver no toca el GPIO y
  lo dice, en vez de fallar en silencio o de mover hierro sin querer.
* Un intervalo minimo entre pulsos evita que un bucle de eventos castigue el
  servo. Un ALLOW mientras la puerta esta abierta **extiende** la ventana en vez
  de volver a pulsar, que es lo que quieres cuando entran dos coches seguidos.
* El cierre automatico se programa siempre. Si el proceso muere antes de
  cerrar, `atexit` deja de pulsar y libera la linea.
* Un unico lock por linea: dos aperturas simultaneas no se pelean por el servo.
"""
from __future__ import annotations

import atexit
import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Margen de un servo de aeromodelismo estandar. Es el valor POR DEFECTO, no un
# limite absoluto: hay servos que aceptan algo mas y recorren mas angulo.
MIN_PULSE_US = 500
MAX_PULSE_US = 2500

# Topes DUROS. Estos si son infranqueables, y la razon no es teorica: mas alla
# del margen util, el servo llega a su tope mecanico y se **cala**. Calado deja
# de girar pero sigue consumiendo al maximo, y en esta Pi eso esta medido: con
# el servo quieto, 0 subtensiones en 70 s; moviendose, 5. Un servo calado tira
# mas que uno girando, asi que ampliar el margen a ciegas es la forma mas rapida
# de reiniciar la Pi y, con ella, la plataforma de la tesis.
#
# 400-2700 us cubre lo que acepta practicamente cualquier servo de hobby. Mas
# alla no se amplia por configuracion: haria falta saber el modelo exacto.
HARD_MIN_PULSE_US = 400
HARD_MAX_PULSE_US = 2700


@dataclass(frozen=True)
class ServoConfig:
    """Lo que necesita el driver, leido de `barrier_controller.config`."""

    chip: int = 0
    pin: int = 17
    freq_hz: int = 50
    closed_us: int = 1000
    open_us: int = 2000
    # Cuanto se pulsa para que el brazo complete el recorrido.
    travel_ms: int = 700
    # Cuanto se queda abierta antes del cierre automatico.
    open_seconds: float = 6.0
    # Margen util de ESTE servo. Ampliarlo hace que recorra mas angulo, a
    # cambio de acercarse a su tope mecanico. Ver los topes duros arriba.
    pulse_min_us: int = MIN_PULSE_US
    pulse_max_us: int = MAX_PULSE_US
    # Suelo entre pulsos de apertura, para no castigar el servo.
    min_interval_s: float = 2.0

    @classmethod
    def from_row(cls, config: dict[str, Any] | None) -> "ServoConfig":
        c = config or {}

        def _i(key: str, default: int) -> int:
            try:
                return int(c[key])
            except (KeyError, TypeError, ValueError):
                return default

        def _f(key: str, default: float) -> float:
            try:
                return float(c[key])
            except (KeyError, TypeError, ValueError):
                return default

        # El margen util se resuelve antes, porque los anchos se limitan a el.
        lo = max(HARD_MIN_PULSE_US,
                 min(HARD_MAX_PULSE_US, _i("pulse_min_us", MIN_PULSE_US)))
        hi = max(HARD_MIN_PULSE_US,
                 min(HARD_MAX_PULSE_US, _i("pulse_max_us", MAX_PULSE_US)))
        if hi - lo < 200:
            # Un margen degenerado dejaria la barrera sin recorrido util.
            lo, hi = MIN_PULSE_US, MAX_PULSE_US
        return cls(
            chip=_i("chip", 0),
            pin=_i("pin", 17),
            freq_hz=max(40, min(500, _i("freq_hz", 50))),
            pulse_min_us=lo,
            pulse_max_us=hi,
            closed_us=max(lo, min(hi, _i("closed_us", 1000))),
            open_us=max(lo, min(hi, _i("open_us", 2000))),
            travel_ms=max(100, min(3000, _i("travel_ms", 700))),
            open_seconds=max(1.0, min(120.0, _f("open_seconds", 6.0))),
            min_interval_s=max(0.0, min(60.0, _f("min_interval_s", 2.0))),
        )


class ServoUnavailable(RuntimeError):
    """La linea GPIO no se puede usar aqui (sin lgpio, sin permisos, ocupada)."""


class ServoGate:
    """Un servo en una linea GPIO. Una instancia por (chip, pin) y proceso."""

    def __init__(self, cfg: ServoConfig) -> None:
        self._cfg = cfg
        self._lock = threading.RLock()
        self._handle: Optional[int] = None
        self._claimed = False
        self._close_timer: Optional[threading.Timer] = None
        self._open_until: float = 0.0
        self._last_open_at: float = 0.0
        self._is_open = False
        self.opens = 0

    # --- acceso a la linea -------------------------------------------------
    def _ensure_line(self) -> int:
        """Abre el chip y reclama la linea. Idempotente."""
        if self._handle is not None and self._claimed:
            return self._handle
        try:
            import lgpio
        except ImportError as exc:
            raise ServoUnavailable(
                "lgpio no esta disponible en este interprete."
            ) from exc

        if self._handle is None:
            try:
                self._handle = lgpio.gpiochip_open(self._cfg.chip)
            except Exception as exc:
                raise ServoUnavailable(
                    f"No se pudo abrir /dev/gpiochip{self._cfg.chip}: {exc}. "
                    "Revisa que el servicio tenga acceso al dispositivo."
                ) from exc
        if not self._claimed:
            try:
                # Arranca en bajo: sin pulsos, el servo queda sin consigna.
                lgpio.gpio_claim_output(self._handle, self._cfg.pin, 0)
                self._claimed = True
            except Exception as exc:
                raise ServoUnavailable(
                    f"No se pudo reclamar GPIO{self._cfg.pin}: {exc}. "
                    "Puede estar en uso por otro proceso."
                ) from exc
            atexit.register(self.shutdown)
        return self._handle

    def _duty_for(self, width_us: int) -> float:
        """Ancho de pulso en microsegundos -> ciclo de trabajo en %."""
        period_us = 1_000_000.0 / max(1, self._cfg.freq_hz)
        return max(0.0, min(100.0, width_us / period_us * 100.0))

    def _pulse(self, width_us: int, hold_s: float) -> None:
        """Transmite el tren de pulsos durante `hold_s` y despues lo corta.

        Cortar es deliberado: ver la cabecera del modulo. Sin pulsos el servo
        no tiembla ni se sobrecalienta.

        Se usa `tx_pwm` y no `tx_servo`, que seria lo obvio, por una razon
        concreta: **`lgpio.tx_servo` rechaza cualquier ancho fuera de
        500-2500 us** con `'bad servo pulsewidth'`. Ese limite es de la
        libreria, no del servo, y dejaba sin explorar el recorrido que algunos
        servos tienen mas alla. `tx_pwm` toma un ciclo de trabajo en por ciento,
        asi que a 50 Hz (periodo de 20 ms) cualquier ancho es expresable: 2700 us
        son el 13.5 %. El tren de pulsos resultante es identico; lo unico que
        cambia es como se le pide.

        `tx_servo` tambien fallaba con `'bad PWM micros'` al pedirle anchura 0
        para parar, asi que parar tambien se hace por aqui.
        """
        import lgpio

        h = self._ensure_line()
        freq = self._cfg.freq_hz
        rc = lgpio.tx_pwm(h, self._cfg.pin, freq, self._duty_for(width_us))
        if rc < 0:
            raise ServoUnavailable(
                f"lgpio.tx_pwm fallo en GPIO{self._cfg.pin} (rc={rc})."
            )
        time.sleep(hold_s)
        # Ciclo de trabajo 0 = dejar de pulsar.
        lgpio.tx_pwm(h, self._cfg.pin, freq, 0)

    # --- operaciones -------------------------------------------------------
    def open(self, reason: str) -> str:
        """Pulsa el servo a la posicion de apertura y programa el cierre.

        Devuelve el detalle que se guarda en el evento. Lanza ServoUnavailable
        si la linea no se puede usar, para que SCAE registre FAILED en vez de
        dar por buena una apertura que no ocurrio.
        """
        cfg = self._cfg
        with self._lock:
            now = time.monotonic()

            # Ya esta abierta: extender la ventana, no volver a pulsar. Dos
            # coches seguidos no deben hacer bajar y subir la pluma.
            if self._is_open:
                self._open_until = now + cfg.open_seconds
                self._schedule_close(cfg.open_seconds)
                return (
                    f"Barrera ya abierta: ventana extendida a {cfg.open_seconds:.0f} s "
                    f"({reason})."
                )

            # Suelo entre pulsos: protege el servo de un bucle de eventos.
            since = now - self._last_open_at
            if self._last_open_at and since < cfg.min_interval_s:
                raise ServoUnavailable(
                    f"Apertura demasiado seguida ({since:.1f} s < "
                    f"{cfg.min_interval_s:.1f} s). No se pulsa el servo."
                )

            self._pulse(cfg.open_us, cfg.travel_ms / 1000.0)
            self._is_open = True
            self._last_open_at = time.monotonic()
            self._open_until = self._last_open_at + cfg.open_seconds
            self.opens += 1
            self._schedule_close(cfg.open_seconds)
            logger.info(
                "Servo GPIO%s pulsado a apertura (%s us). Motivo: %s",
                cfg.pin, cfg.open_us, reason,
            )
            # "pulsos transmitidos", no "barrera abierta": no hay sensor.
            return (
                f"Servo GPIO{cfg.pin}: pulsos de apertura transmitidos "
                f"({cfg.open_us} us, {cfg.travel_ms} ms). Cierre automatico en "
                f"{cfg.open_seconds:.0f} s. Sin sensor de posicion."
            )

    def update_config(self, cfg: ServoConfig) -> None:
        """Cambia los anchos de pulso conservando la linea ya reclamada.

        PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

        La primera version de la calibracion tiraba la instancia viva y dejaba
        que se creara otra con la configuracion nueva. Parecia limpio y no lo
        era: `shutdown()` fallaba a medias, el fallo se tragaba en un `except`
        a nivel DEBUG, y la instancia nueva se encontraba la linea reclamada
        por la vieja. El sintoma era un `GPIO busy` en la siguiente apertura
        real, con el proceso teniendo DOS descriptores sobre /dev/gpiochip0.

        Asi que la configuracion se cambia en sitio y la linea no se suelta
        nunca: un solo dueno del pin mientras vive el proceso, que es lo que
        esta clase pretendia desde el principio.

        Cambiar de chip o de pin si obliga a soltar y volver a reclamar, y eso
        se hace fuera, recreando la instancia a proposito.
        """
        if (cfg.chip, cfg.pin) != (self._cfg.chip, self._cfg.pin):
            raise ValueError(
                "update_config no puede cambiar de linea GPIO; "
                "para eso hay que recrear el ServoGate."
            )
        with self._lock:
            self._cfg = cfg
            logger.info(
                "Servo GPIO%s recalibrado: cerrado %s us, abierto %s us, "
                "viaje %s ms", cfg.pin, cfg.closed_us, cfg.open_us, cfg.travel_ms,
            )

    def jog(self, pulse_us: int, hold_ms: int = 600) -> dict:
        """Mueve el servo a un ancho de pulso CRUDO. Solo para calibrar.

        PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

        No pasa por la maquina de estados de apertura/cierre: no marca la
        barrera como abierta, no programa ningun cierre y no cuenta como
        apertura. Es exactamente lo que hace falta para encontrar los topes del
        servo, y exactamente lo que NO debe usarse para dejar pasar un coche.

        El ancho se limita a 500-2500 us. Fuera de ese margen se puede llevar el
        servo contra su tope mecanico, donde se cala: deja de girar pero sigue
        consumiendo, y en una Pi con la fuente justa eso puede provocar una
        caida de tension.
        """
        width = max(self._cfg.pulse_min_us,
                    min(self._cfg.pulse_max_us, int(pulse_us)))
        hold = max(0.05, min(3.0, hold_ms / 1000.0))
        with self._lock:
            # Un jog mientras la barrera esta abierta cancelaria el cierre
            # automatico y dejaria la pluma arriba sin que nadie la baje.
            if self._is_open:
                raise ServoUnavailable(
                    "La barrera esta abierta ahora mismo. Espera a que cierre "
                    "antes de calibrar."
                )
            self._pulse(width, hold)
            logger.info("Servo GPIO%s: jog de calibracion a %s us", self._cfg.pin, width)
            return {
                "pulse_us": width,
                "requested_us": int(pulse_us),
                "clamped": width != int(pulse_us),
                "hold_ms": int(hold * 1000),
                # Los grados se estiman sobre el margen util de ESTE servo:
                # con 500-2500 los 180 grados caen donde se espera; con un
                # margen ampliado, el mismo pulso significa otro angulo.
                "approx_degrees": round(
                    (width - self._cfg.pulse_min_us)
                    / max(1, self._cfg.pulse_max_us - self._cfg.pulse_min_us)
                    * 180, 1,
                ),
                "pulse_min_us": self._cfg.pulse_min_us,
                "pulse_max_us": self._cfg.pulse_max_us,
            }

    def close(self, reason: str = "cierre automatico") -> str:
        cfg = self._cfg
        with self._lock:
            self._cancel_timer()
            try:
                self._pulse(cfg.closed_us, cfg.travel_ms / 1000.0)
            except ServoUnavailable:
                logger.exception("No se pudo pulsar el cierre en GPIO%s", cfg.pin)
                raise
            self._is_open = False
            self._open_until = 0.0
            logger.info("Servo GPIO%s pulsado a cierre (%s)", cfg.pin, reason)
            return f"Servo GPIO{cfg.pin}: pulsos de cierre transmitidos ({reason})."

    def _schedule_close(self, delay_s: float) -> None:
        self._cancel_timer()
        t = threading.Timer(delay_s, self._auto_close)
        t.name = f"scae-servo-close-{self._cfg.pin}"
        t.daemon = True
        self._close_timer = t
        t.start()

    def _cancel_timer(self) -> None:
        t = self._close_timer
        if t is not None:
            t.cancel()
            self._close_timer = None

    def _auto_close(self) -> None:
        with self._lock:
            # Otra apertura pudo extender la ventana mientras el timer dormia.
            if time.monotonic() < self._open_until - 0.05:
                self._schedule_close(self._open_until - time.monotonic())
                return
        try:
            self.close("cierre automatico por tiempo")
        except Exception:
            logger.exception("Fallo el cierre automatico del servo")

    # --- estado y apagado --------------------------------------------------
    @property
    def state(self) -> dict[str, Any]:
        with self._lock:
            remaining = max(0.0, self._open_until - time.monotonic())
            return {
                "chip": self._cfg.chip,
                "pin": self._cfg.pin,
                "is_open": self._is_open,
                "closes_in_s": round(remaining, 1) if self._is_open else None,
                "opens": self.opens,
                "line_claimed": self._claimed,
            }

    def shutdown(self) -> None:
        """Deja de pulsar y libera la linea. Seguro de llamar varias veces."""
        with self._lock:
            self._cancel_timer()
            if self._handle is None:
                return
            try:
                import lgpio

                if self._claimed:
                    # tx_pwm, no tx_servo: ver _pulse(). Con tx_servo esto
                    # fallaba con 'bad PWM micros' y dejaba la linea reclamada.
                    lgpio.tx_pwm(self._handle, self._cfg.pin, self._cfg.freq_hz, 0)
                    lgpio.gpio_free(self._handle, self._cfg.pin)
                lgpio.gpiochip_close(self._handle)
            except Exception:
                # A nivel DEBUG esto era invisible con el log en INFO, y un
                # fallo aqui deja la linea reclamada por un gestor que ya nadie
                # tiene: la siguiente apertura real falla con 'GPIO busy'.
                logger.warning(
                    "Fallo liberando el servo en GPIO%s; la linea puede quedar "
                    "reclamada hasta que el proceso termine", self._cfg.pin,
                    exc_info=True,
                )
            finally:
                self._claimed = False
                self._handle = None


# --------------------------------------------------------------------------
# Una instancia por linea fisica, compartida por todas las filas que la usen
# --------------------------------------------------------------------------
_gates: dict[tuple[int, int], ServoGate] = {}
_gates_lock = threading.Lock()


def get_gate(cfg: ServoConfig) -> ServoGate:
    key = (cfg.chip, cfg.pin)
    with _gates_lock:
        gate = _gates.get(key)
        if gate is None:
            gate = ServoGate(cfg)
            _gates[key] = gate
        return gate


def known_gates() -> list[dict[str, Any]]:
    with _gates_lock:
        return [g.state for g in _gates.values()]
