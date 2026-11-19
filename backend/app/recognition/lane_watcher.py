"""Reconocimiento automatico de placas sobre el video en vivo.

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

Que hace
--------
Toma fotogramas de la camara, busca una placa, la lee, y mete el resultado por
`access.process_capture(..., source=EventSource.CAMERA)`. A partir de ahi es el
SCAE de siempre: normalizacion mexicana, motor de decision con sus nueve reglas,
registro de evento, evidencia y auditoria. **No hay un segundo motor de
decision**: esto solo alimenta el que ya existe.

Decisiones de diseno
--------------------
* **Comparte el proceso de captura de la vista en vivo.** No abre una segunda
  conexion RTSPS. Si lo hiciera, habria dos decodificaciones del mismo video en
  una Pi que comparte hardware con la plataforma de monitoreo energetico de la
  tesis. Decodificar una vez y repartir es la diferencia entre ~35 % de un
  nucleo y ~70 %.

* **Puerta de movimiento antes de inferir.** En una caseta no pasa nada durante
  minutos. Comparar dos miniaturas en escala de grises cuesta microsegundos;
  ejecutar detector + OCR cuesta ~150 ms. Sin esta puerta estariamos gastando
  CPU permanentemente para mirar un carril vacio.

* **Muestreo bajo.** Por defecto 2 fotogramas por segundo para inferencia,
  independiente de los 24 fps de la vista en vivo. Un vehiculo que entra a una
  caseta esta en cuadro varios segundos: no hace falta mirar cada fotograma.

* **Corre en un hilo, no en el bucle de asyncio.** La inferencia ONNX es trabajo
  de CPU que bloquea; en el event loop congelaria la API mientras tanto.

* **Se puede parar.** El guardia o el administrador pueden detener el
  procesamiento sin cortar el video, y al reves.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


def _unreadable_note(
    guess: Optional[str], consistency: Optional[float], samples: int,
) -> str:
    """Nota del evento cuando no se consigue identificar la placa.

    Incluye la mejor conjetura y cuanto la respaldan las lecturas. Decirle al
    guardia "lo mas probable es VTC334A, 3 de 5 lecturas coincidieron" le da por
    donde empezar a teclear y cuanto fiarse; un "placa ilegible" a secas no le
    da nada.
    """
    base = ("Vehiculo detectado en el carril sin poder identificar la placa. "
            "Requiere que el guardia la teclee.")
    if not guess:
        return base
    if samples < 2:
        # Con una sola lectura el porcentaje es trivialmente 100 % y transmite
        # una confianza que no existe. Se da la conjetura y se dice que es una.
        return f"{base} Unica lectura obtenida: {guess} (sin confirmar)."
    pct = round((consistency or 0) * 100)
    return (
        f"{base} Mejor conjetura: {guess} "
        f"({pct} % de {samples} lecturas coincidieron; NO confirmada)."
    )


@dataclass
class WatcherStatus:
    enabled: bool = False
    state: str = "stopped"          # stopped|idle|watching|error
    detail: str = ""
    frames_seen: int = 0
    frames_analysed: int = 0        # los que pasaron la puerta de movimiento
    plates_detected: int = 0        # placas VISTAS por el detector
    plates_read: int = 0            # de esas, las que se pudieron LEER
    plates_unreadable: int = 0      # eventos creados por placa ilegible
    plates_too_small: int = 0       # vistas pero demasiado pequenas para leer
    # Mejor conjetura cuando no se consigue confirmar, y que proporcion de las
    # lecturas recientes la respaldo.
    last_guess: Optional[str] = None
    last_consistency: Optional[float] = None
    events_created: int = 0
    suppressed: int = 0             # lecturas descartadas por deduplicacion
    last_plate: Optional[str] = None
    last_confidence: Optional[float] = None
    last_decision: Optional[str] = None
    last_event_id: Optional[int] = None
    last_at: Optional[float] = None
    avg_inference_ms: float = 0.0
    # --- geometria para el overlay de la vista en vivo -------------------
    # Lo ultimo que el detector vio, en coordenadas del fotograma. El frontend
    # lo escala al tamano con el que pinta el <img>. Se sirve con marca de
    # tiempo para que la consola pueda descartar cajas viejas en vez de dejar
    # un rectangulo flotando sobre un carril ya vacio.
    overlay_at: float = 0.0
    overlay_w: int = 0
    overlay_h: int = 0
    overlay_vehicles: list[dict] = field(default_factory=list)
    overlay_plates: list[dict] = field(default_factory=list)
    vehicles_seen: int = 0
    _times: list[float] = field(default_factory=list)


@dataclass
class _Pending:
    """Lecturas acumuladas de una placa que aun no se ha confirmado."""
    reads: int = 0
    first_at: float = 0.0
    best_confidence: float = 0.0
    best_frame: Optional[bytes] = None
    # El analisis que ya se hizo de `best_frame`, para no repetirlo al emitir.
    best_recognition: object = None


class LaneWatcher:
    """Vigila el carril y crea eventos de acceso cuando reconoce una placa."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.status = WatcherStatus()
        # placa_normalizada -> momento en que se emitio el ultimo evento
        self._emitted: dict[str, float] = {}
        # placa_normalizada -> lecturas acumuladas pendientes de confirmar
        self._pending: dict[str, _Pending] = {}
        self._prev_gray: Optional[np.ndarray] = None
        self._analysed = 0
        self._last_analysis_at = 0.0
        # Analisis seguidos con una placa A LA VISTA que no han producido
        # ninguna identificacion utilizable (ni ilegible, ni confirmada).
        self._no_id_streak = 0
        self._last_no_id_at = 0.0
        # Lecturas recientes (texto, momento), para la conjetura mayoritaria.
        self._recent_reads: list[tuple[str, float]] = []
        # Tras avisar, se espera a que el carril quede despejado antes de
        # volver a avisar. Es lo que evita cien avisos del mismo coche sin
        # imponer ninguna espera al siguiente vehiculo.
        self._awaiting_clear = False
        self._clear_streak = 0

    # --- configuracion -----------------------------------------------------
    @staticmethod
    def _cfg() -> dict:
        from app.hardware.camera_stream import get_stream

        c = get_stream()._cfg
        def _i(k, d):
            try:
                return int(c.get(k, d))
            except (TypeError, ValueError):
                return d
        def _f(k, d):
            try:
                return float(c.get(k, d))
            except (TypeError, ValueError):
                return d
        return {
            "sample_fps": _f("SCAE_LPR_SAMPLE_FPS", 2.0),
            "motion_threshold": _f("SCAE_LPR_MOTION_THRESHOLD", 2.5),
            # Un coche que se DETIENE ante la barrera deja de generar
            # movimiento, y la puerta de movimiento dejaria de mirarlo justo
            # cuando mas falta hace. Esto fuerza un analisis cada tantos
            # segundos aunque el carril parezca quieto. A 0 se desactiva.
            "idle_scan_seconds": _f("SCAE_LPR_IDLE_SCAN_SECONDS", 4.0),
            # Vehiculo con placa VISIBLE pero ILEGIBLE. Tras tantos analisis
            # seguidos viendo una placa sin poder leerla, se crea el evento
            # igualmente para que el guardia intervenga. A 0 se desactiva.
            "unreadable_after": _i("SCAE_LPR_UNREADABLE_AFTER", 4),
            # Ancho minimo de la caja de la placa para intentar leerla.
            #
            # Idea tomada de la implementacion paralela que recibio el operador,
            # que usa 90 px: por debajo de cierto tamano el OCR no tiene pixeles
            # suficientes y devuelve caracteres inventados, que es peor que no
            # devolver nada. Aqui el valor por defecto es 70 y no 90 porque
            # nuestra camara entrega 1280x720 -no fotos de movil de 1200x1600- y
            # la placa de la maqueta mide 111-127 px: 90 dejaria poco margen.
            "min_plate_px": _i("SCAE_LPR_MIN_PLATE_PX", 70),
            "unreadable_cooldown": _f("SCAE_LPR_UNREADABLE_COOLDOWN", 60.0),
            # Cuanto tiempo un aviso sin atender silencia los siguientes. Pasada
            # la ventana se vuelve a avisar aunque la cola siga sucia: un evento
            # viejo y abandonado no puede callar el carril para siempre.
            "pending_window": _f("SCAE_LPR_PENDING_WINDOW", 0.0),
            # Analisis seguidos SIN placa a la vista para dar el carril por
            # despejado y volver a avisar. Sustituye a los limites de tiempo:
            # lo que habilita el siguiente aviso es que el vehiculo se haya
            # ido, no que pase un reloj.
            "clear_frames": _i("SCAE_LPR_CLEAR_FRAMES", 2),
            "dedup_seconds": _f("SCAE_LPR_DEDUP_SECONDS", 15.0),
            "confirm_reads": _i("SCAE_LPR_CONFIRM_READS", 2),
            "confirm_window": _f("SCAE_LPR_CONFIRM_WINDOW", 6.0),
            "autostart": c.get("SCAE_LPR_AUTOSTART", "false").lower() == "true",
            # Deteccion de vehiculos para el overlay (modelo aparte del de
            # placas). Medido en esta Pi: 158 ms con yolox-s int8.
            "vehicle_detect": c.get("SCAE_LPR_VEHICLE_DETECT", "true").lower() == "true",
            "vehicle_model": c.get("SCAE_LPR_VEHICLE_MODEL", "yolo11n-320-coco.onnx"),
            # La arquitectura va junto al modelo: el postprocesado de YOLOX y el
            # de YOLO11 no son intercambiables y confundirlos da cero
            # detecciones en silencio.
            "vehicle_arch": c.get("SCAE_LPR_VEHICLE_ARCH", "yolo11"),
            "vehicle_classes": c.get("SCAE_LPR_VEHICLE_CLASSES", "") or None,
            "vehicle_min_conf": _f("SCAE_LPR_VEHICLE_MIN_CONF", 0.35),
            # Cada cuantos fotogramas analizados se busca el vehiculo. A 1 se
            # suman 158 ms a cada analisis; a 2 la media baja a ~79 ms y el
            # overlay sigue refrescando aproximadamente una vez por segundo,
            # que para una referencia visual sobra.
            "vehicle_every": max(1, _i("SCAE_LPR_VEHICLE_EVERY", 2)),
            "lane_id": _i("SCAE_LPR_LANE_ID", 0) or None,
            "camera_id": _i("SCAE_LPR_CAMERA_ID", 0) or None,
        }

    # --- ciclo de vida -----------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return False
            self._stop.clear()
            self.status.enabled = True
            self._thread = threading.Thread(
                target=self._run, name="scae-lpr", daemon=True
            )
            self._thread.start()
            return True

    def stop(self) -> bool:
        with self._lock:
            if self._thread is None or not self._thread.is_alive():
                self.status.enabled = False
                return False
        self._stop.set()
        self.status.enabled = False
        return True

    @property
    def running(self) -> bool:
        t = self._thread
        return t is not None and t.is_alive()

    # --- bucle -------------------------------------------------------------
    def _run(self) -> None:
        from app.hardware.camera_stream import get_stream

        cfg = self._cfg()
        stream = get_stream()
        if not stream.configured:
            self._set("error", "No hay camara configurada.")
            self.status.enabled = False
            return

        # Contar como espectador mantiene viva la captura compartida mientras
        # vigilamos, sin abrir una segunda conexion a la camara.
        stream.acquire()
        self._set("idle", "Esperando vehiculos")
        interval = 1.0 / max(0.1, cfg["sample_fps"])
        last_seq = -1
        try:
            next_at = time.monotonic()
            while not self._stop.is_set():
                # Dormir solo lo que FALTA para el siguiente muestreo, no el
                # intervalo entero.
                #
                # Antes era `time.sleep(interval)` a secas, antes de analizar,
                # asi que cada vuelta costaba intervalo + inferencia: con 500 ms
                # de intervalo y ~840 ms de inferencia, un ciclo de 1.34 s en vez
                # de 0.84. Como hacen falta varios analisis antes de avisar al
                # guardia, ese medio segundo se multiplicaba y era una parte
                # grande del tiempo hasta que aparecia el aviso.
                now = time.monotonic()
                if next_at > now:
                    # El stop corta la espera: parar no deberia tardar un ciclo.
                    if self._stop.wait(next_at - now):
                        break
                next_at = max(time.monotonic(), next_at + interval)
                seq, frame = stream.peek()
                if frame is None or seq == last_seq:
                    continue
                last_seq = seq
                self.status.frames_seen += 1
                try:
                    self._handle(frame, cfg)
                except Exception as exc:  # pragma: no cover
                    logger.exception("Fallo analizando fotograma")
                    self._set("error", str(exc)[:160])
        finally:
            stream.release()
            self._prev_gray = None
            self._set("stopped", "Procesamiento detenido")
            self.status.enabled = False

    def _handle(self, jpeg: bytes, cfg: dict) -> None:
        from app.recognition.pipeline import decode_image

        frame = decode_image(jpeg)
        if frame is None:
            return

        # --- puerta de movimiento -----------------------------------------
        gray = frame[::8, ::8, 1].astype(np.int16)  # submuestreo barato
        moved = True
        if self._prev_gray is not None and self._prev_gray.shape == gray.shape:
            diff = float(np.abs(gray - self._prev_gray).mean())
            moved = diff >= cfg["motion_threshold"]
        self._prev_gray = gray

        # Barrido de cortesia: aunque no haya movimiento, mirar de vez en
        # cuando. Un vehiculo parado delante de la barrera -el caso normal en
        # una caseta- no mueve un pixel, y sin esto el sistema dejaria de
        # mirarlo en cuanto frenara. Tambien mantiene el overlay vivo sobre una
        # escena estatica.
        idle_scan = cfg["idle_scan_seconds"]
        if not moved and idle_scan > 0:
            if time.time() - self._last_analysis_at >= idle_scan:
                moved = True

        if not moved:
            if self.status.state != "idle":
                self._set("idle", "Sin movimiento en el carril")
                # Sin movimiento no hay nada que senalar: vaciar el overlay
                # evita dejar un rectangulo sobre un carril ya vacio.
                self.status.overlay_vehicles = []
                self.status.overlay_plates = []
            return

        self._set("watching", "Analizando")
        self.status.frames_analysed += 1
        self._last_analysis_at = time.time()

        from app.recognition.pipeline import get_pipeline

        pipe = get_pipeline()
        t0 = time.perf_counter()
        # Puerta barata: si el detector no ve ninguna placa, no seguimos.
        #
        # RecognitionPipeline.process(), cuando no hay cajas, ejecuta el OCR
        # sobre el fotograma ENTERO. Tiene sentido con el detector original,
        # que buscaba vehiculos (una camara LPR dedicada entrega un recorte de
        # placa donde no hay ningun "coche" que detectar). Con un detector de
        # PLACAS no lo tiene: si no hay placa, no hay nada que leer, y el OCR
        # sobre la escena completa costaba 356 ms para devolver nada. Medido:
        # el carril vacio baja de ~356 ms a ~90 ms por fotograma analizado.
        boxes = pipe.detector.detect(frame)

        # --- deteccion de vehiculos para el overlay -----------------------
        # Va DESPUES del detector de placas y nunca condiciona la lectura: una
        # placa puede estar perfectamente visible sin que el detector de
        # vehiculos acierte (vista parcial, recorte cercano, un modelo a
        # escala). Encadenarlos -no leer la placa si no hay coche- perderia
        # lecturas validas, asi que son independientes a proposito.
        self._analysed += 1
        vehicles: list = []
        # El detector de vehiculos (~200 ms) es SOLO para el overlay: no decide
        # nada. Mientras hay una placa en cuadro estamos en el camino critico
        # -identificar cuanto antes para avisar al guardia- y ademas el overlay
        # ya tiene la caja de la placa, que es mas informativa. Asi que se salta
        # en esos fotogramas y se ejecuta cuando no hay placa, que es justo
        # cuando el recuadro del vehiculo es lo unico que dice "hay algo ahi".
        want_vehicle = (
            cfg["vehicle_detect"]
            and not boxes
            and self._analysed % cfg["vehicle_every"] == 0
        )
        if want_vehicle:
            try:
                from app.recognition.vehicle_backend import (
                    get_vehicle_detector, parse_classes,
                )

                vehicles = get_vehicle_detector(
                    cfg["vehicle_model"],
                    classes=parse_classes(cfg["vehicle_classes"]),
                    conf_threshold=cfg["vehicle_min_conf"],
                    arch=cfg["vehicle_arch"],
                ).detect(frame)
                if vehicles:
                    self.status.vehicles_seen += 1
            except Exception as exc:
                # El overlay es una ayuda visual: si el modelo de vehiculos
                # falla, el reconocimiento de placas tiene que seguir.
                logger.warning("Deteccion de vehiculos no disponible: %s", exc)
                cfg["vehicle_detect"] = False
            self._publish_overlay(frame, vehicles, boxes, None)
        elif boxes:
            self._publish_overlay(frame, None, boxes, None)

        if not boxes:
            # Carril sin placa a la vista: si estabamos esperando a que se
            # despejara, esto cuenta.
            self._clear_streak += 1
            if self._awaiting_clear and self._clear_streak >= cfg["clear_frames"]:
                self._awaiting_clear = False
                logger.debug("Carril despejado: listo para volver a avisar")
            self._record_time((time.perf_counter() - t0) * 1000)
            return
        # Hay placa: el carril no esta despejado.
        self._clear_streak = 0
        # Separar "no se ve ninguna placa" de "se ve y no se puede leer" es la
        # diferencia entre un problema de encuadre y uno de enfoque o de luz.
        self.status.plates_detected += 1

        # Placa demasiado pequena para leerla con garantias: no se gasta el OCR
        # en inventar caracteres. Cuenta como "no identificada", asi que el
        # guardia acaba recibiendo el aviso igual, pero sin lecturas basura por
        # el medio.
        widest = max(b.x2 - b.x1 for b in boxes)
        if cfg["min_plate_px"] > 0 and widest < cfg["min_plate_px"]:
            self.status.plates_too_small += 1
            self._publish_overlay(frame, None, boxes, None)
            self._record_time((time.perf_counter() - t0) * 1000)
            self._no_identification(
                jpeg, cfg, time.time(),
                recognition=self._recognition_of(boxes, None, 0.0),
            )
            return

        # Se reaprovechan las cajas ya detectadas en vez de llamar a
        # pipe.process(), que volveria a ejecutar el detector sobre el mismo
        # fotograma. Medido: con un vehiculo delante se pasaba de ~310 ms a
        # ~827 ms por el doble pase, justo en el momento en que mas importa
        # ir rapido. Esto replica el bucle de RecognitionPipeline.process:
        # leer las placas mas grandes y parar en cuanto una sea valida.
        from app.recognition.plate import best_reading

        fragments: list[tuple[str, float]] = []
        for box in boxes[:3]:
            fragments.extend(pipe.reader.read(box.crop(frame)))
            if best_reading(fragments).is_valid:
                break
        reading = best_reading(fragments)
        elapsed = (time.perf_counter() - t0) * 1000
        self._record_time(elapsed)
        s = self.status

        now = time.time()
        if not reading or not reading.is_valid:
            # Hay una placa delante y no se puede leer.
            #
            # Antes esto terminaba aqui en silencio, y era el agujero mas
            # grande del carril automatico: un coche parado ante la pluma con
            # la placa sucia, de noche o mal enfocada no generaba NADA. El
            # guardia no se enteraba de que habia alguien esperando, que es
            # justo el momento en el que mas falta hace un humano.
            #
            # Ahora, tras varios analisis seguidos sin lectura, se crea el
            # evento de todos modos. El motor de decision lo resuelve como
            # MANUAL_REVIEW / NO_PLATE_READ y el guardia puede teclear la placa
            # y darle entrada. El contador y el tiempo de espera estan para que
            # un carril con un camion aparcado enfrente no genere un evento por
            # segundo.
            self._no_identification(
                jpeg, cfg, now,
                recognition=self._recognition_of(boxes, reading, elapsed),
            )
            return
        plate = reading.normalized
        confidence = float(reading.confidence or 0.0)
        s.plates_read += 1
        s.last_plate = plate
        s.last_confidence = round(confidence, 4)
        s.last_at = now
        # Se guarda para la conjetura mayoritaria: con una placa dificil casi
        # ninguna lectura confirma, pero la mas repetida sigue siendo la mejor
        # pista que el sistema le puede dar al guardia.
        self._recent_reads.append((plate, now))
        if len(self._recent_reads) > 40:
            del self._recent_reads[:-40]
        # Repintar el overlay ya con el texto, para que el guardia vea la
        # lectura sobre la propia placa y no solo en el panel lateral.
        self._publish_overlay(frame, None, boxes, (plate, confidence))

        # --- deduplicacion -------------------------------------------------
        # Una camara ve el mismo vehiculo durante varios segundos. Sin esto,
        # SCAE generaria decenas de eventos para un solo coche.
        last_emit = self._emitted.get(plate)
        if last_emit is not None and now - last_emit < cfg["dedup_seconds"]:
            s.suppressed += 1
            return

        # --- confirmacion --------------------------------------------------
        # Exigir varias lecturas coherentes evita crear un evento por una
        # lectura suelta y dudosa. La ventana impide que dos lecturas separadas
        # por minutos cuenten como confirmacion.
        pend = self._pending.get(plate)
        if pend is None or now - pend.first_at > cfg["confirm_window"]:
            pend = _Pending(reads=0, first_at=now)
            self._pending[plate] = pend
        pend.reads += 1
        if confidence >= pend.best_confidence:
            pend.best_confidence = confidence
            pend.best_frame = jpeg
            pend.best_recognition = self._recognition_of(boxes, reading, elapsed)
        if pend.reads < cfg["confirm_reads"]:
            # Se leyo algo, pero todavia no hay dos lecturas coherentes. Si esto
            # se repite, el guardia tiene que enterarse: ver _no_identification.
            self._no_identification(
                jpeg, cfg, now,
                recognition=self._recognition_of(boxes, reading, elapsed),
            )
            return

        # Confirmada: crear el evento por el flujo normal de SCAE.
        self._pending.pop(plate, None)
        self._emitted[plate] = now
        self._no_id_streak = 0
        self._awaiting_clear = True
        self._clear_streak = 0
        self._emit(
            pend.best_frame or jpeg, cfg,
            recognition=pend.best_recognition
            or self._recognition_of(boxes, reading, elapsed),
        )
        # Limpieza: no acumular placas vistas hace mucho.
        cutoff = now - max(cfg["dedup_seconds"] * 4, 120)
        for k in [k for k, v in self._emitted.items() if v < cutoff]:
            self._emitted.pop(k, None)

    def _recognition_of(self, boxes: list, reading, elapsed_ms: float):
        """Empaqueta lo ya analizado como un RecognitionResult.

        Asi `process_capture` se salta el detector y el OCR en vez de repetirlos
        sobre el mismo JPEG. Medido en el log antes de esto: 1.00 s entre que el
        vigilante decidia avisar y que el evento existia, con el coche parado
        delante de la pluma. Es el mismo doble pase del parche 18, un nivel mas
        abajo.
        """
        from app.recognition.pipeline import RecognitionResult, get_pipeline

        pipe = get_pipeline()
        return RecognitionResult(
            reading=reading,
            boxes=list(boxes),
            detector_name=getattr(pipe.detector, "name", ""),
            ocr_name=getattr(pipe.reader, "name", ""),
            elapsed_ms=int(elapsed_ms),
        )

    def _emit(
        self, jpeg: bytes, cfg: dict, *, unreadable: bool = False,
        recognition=None, guess: Optional[str] = None,
        consistency: Optional[float] = None, samples: int = 0,
    ) -> None:
        from app.core.db import SessionLocal
        from app.models import Camera, EventSource, Lane
        from app.services import access, audit

        db = SessionLocal()
        try:
            camera = None
            lane = None
            if cfg["camera_id"]:
                camera = db.get(Camera, cfg["camera_id"])
            if camera is None:
                camera = db.query(Camera).filter(Camera.is_active.is_(True)).first()
            if cfg["lane_id"]:
                lane = db.get(Lane, cfg["lane_id"])
            if lane is None and camera is not None:
                lane = db.get(Lane, camera.lane_id)
            if lane is None:
                lane = db.query(Lane).first()

            event = access.process_capture(
                db,
                image_bytes=jpeg,
                lane=lane,
                actor=None,                     # captura automatica, sin operador
                source=EventSource.CAMERA,
                camera=camera,
                precomputed=recognition,
                notes=(
                    _unreadable_note(guess, consistency, samples)
                    if unreadable
                    else "Captura automatica de la camara del carril."
                ),
            )
            audit.record(
                db, action="events.camera_capture", actor=None,
                entity_type="access_event", entity_id=event.id,
                summary=f"{event.decision.value} - {event.plate_normalized or 'sin placa'}",
            )
            db.commit()
            db.refresh(event)
            s = self.status
            s.events_created += 1
            s.last_event_id = event.id
            s.last_decision = event.decision.value
            logger.info(
                "Evento automatico %s: %s -> %s",
                event.id, event.plate_normalized, event.decision.value,
            )
        except Exception:
            db.rollback()
            logger.exception("No se pudo registrar el evento automatico")
        finally:
            db.close()

    def _no_identification(
        self, jpeg: bytes, cfg: dict, now: float, recognition=None,
    ) -> None:
        """Hay una placa delante y el sistema no consigue identificarla.

        Cubre los DOS casos que, desde la caseta, son el mismo problema:

        * no se obtiene ninguna lectura valida (placa sucia, de noche, con un
          angulo imposible), y
        * se obtienen lecturas validas pero **ninguna coincide con otra**, asi
          que la confirmacion de dos lecturas no llega nunca.

        El segundo era el agujero de verdad, y el mas enganoso: los contadores
        decian "5 placas leidas" mientras el guardia no recibia absolutamente
        nada, porque cada lectura salia distinta (VTC384A, WTC3844, WIC3344...
        para una placa que pone FTC-224-A). Un coche esperando ante la pluma y
        una consola en blanco.

        Lo correcto NO es relajar la confirmacion -conceder el acceso con una
        lectura que no se sostiene seria mucho peor- sino avisar al guardia de
        que hay alguien ahi que el sistema no sabe identificar. El evento sale
        como MANUAL_REVIEW y el guardia teclea la placa.

        El tiempo de espera evita que un vehiculo aparcado enfrente genere un
        evento por segundo.
        """
        self._no_id_streak += 1
        if cfg["unreadable_after"] <= 0:
            return
        if self._no_id_streak < cfg["unreadable_after"]:
            return
        if cfg["unreadable_cooldown"] > 0 and (
            now - self._last_no_id_at < cfg["unreadable_cooldown"]
        ):
            return
        # Ya se aviso de este vehiculo y todavia no se ha ido: no se repite.
        #
        # Esto sustituye a los limites de tiempo que habia antes (un
        # temporizador de 60 s y despues una ventana de pendientes). El
        # operador pidio que el aviso salga **en cuanto se ve una placa**, sin
        # esperas, y tenia razon en que una espera fija es lo peor de los dos
        # mundos: tarda con un coche nuevo y no protege de nada que la presencia
        # no proteja mejor.
        #
        # Con esto, un vehiculo que llega dispara el aviso en el primer analisis
        # con placa; mientras siga delante no se repite; y en cuanto se va, el
        # siguiente vuelve a avisar al instante.
        if self._awaiting_clear:
            return
        # Si el guardia ya tiene un aviso sin resolver de este carril, no se le
        # manda otro.
        #
        # Esta es la proteccion CORRECTA contra inundar la consola, y sustituye
        # a lo que hacia el temporizador de 60 s. Medido en el log, ese
        # temporizador era justo lo que hacia que el aviso tardara: los eventos
        # salian a las 18:37:47, 18:38:52, 18:39:56, 18:40:57 -cada 60-65 s-
        # mientras el detector veia la placa continuamente. El vehiculo estaba
        # delante desde el primer segundo y el guardia se enteraba al minuto.
        #
        # Con esta regla el aviso puede salir enseguida: lo que impide la
        # avalancha no es esperar, sino que ya haya uno pendiente de atender.
        # En cuanto el guardia lo resuelve, el siguiente vehiculo avisa al
        # momento.
        if self._has_pending_review(cfg):
            return
        # (El rearme por movimiento que habia aqui se retiro: la guarda de
        # presencia -esperar a que el carril se despeje- cubre el mismo caso del
        # coche aparcado y ademas no impone ninguna espera al vehiculo
        # siguiente. Tener las dos solo conseguia que, despejado el carril, el
        # aviso siguiera bloqueado por falta de movimiento.)
        # SIN NINGUNA LECTURA NO SE AVISA.
        #
        # Antes se creaba el evento igualmente, con la placa vacia, y el guardia
        # recibia un formulario que decia "la camara leyo: nada legible" y un
        # campo en blanco. Eso no es ayuda: no le dice nada que no vea por la
        # ventana, y le llena la cola de eventos que solo puede cerrar a mano.
        #
        # Que la lectura sea MALA no es motivo para callarse -una placa leida al
        # 3 % sigue siendo por donde empezar a teclear- pero que no haya NINGUNA
        # si lo es.
        #
        # Esta comprobacion va ANTES de tocar el estado. Puesta despues -como
        # estaba al principio- marcaba el carril como "esperando despeje" sin
        # haber avisado, y eso **bloqueaba el aviso siguiente**, el legitimo,
        # hasta que el vehiculo se fuera. Tambien inflaba el contador de
        # ilegibles con avisos que nunca salieron: se vio porque
        # `plates_unreadable` marcaba 1 con `events_created` en 0.
        guess, consistency, samples = self._majority_reading(now, cfg)
        if not guess:
            logger.debug("Placa a la vista pero sin ninguna lectura: no se avisa")
            return

        self._no_id_streak = 0
        self._last_no_id_at = now
        self._awaiting_clear = True
        self._clear_streak = 0
        self.status.plates_unreadable += 1

        # Decirle al guardia "lo mas probable es VTC334A, 3 de 5 lecturas
        # coincidieron" le da por donde empezar a teclear y cuanto fiarse.
        self.status.last_guess = guess
        self.status.last_consistency = consistency
        logger.info(
            "Placa a la vista sin identificacion tras %s analisis "
            "(mejor conjetura: %s, %s%% de %s lecturas): se pide intervencion "
            "del guardia",
            cfg["unreadable_after"], guess or "ninguna",
            round((consistency or 0) * 100), samples,
        )
        self._emit(
            jpeg, cfg, unreadable=True, recognition=recognition,
            guess=guess, consistency=consistency, samples=samples,
        )

    def _has_pending_review(self, cfg: dict) -> bool:
        """Hay un aviso RECIENTE de este carril esperando al guardia?

        La ventana es lo importante. La primera version pregunto por cualquier
        evento sin resolver, y eso silenciaba el carril **para siempre**: con
        cien eventos viejos acumulados de las pruebas, ningun vehiculo nuevo
        habria vuelto a avisar. Un aviso de hace una hora -de otro turno, o
        abandonado- no puede callar a un coche que llega ahora.

        Asi que solo cuenta lo reciente: si el guardia acaba de recibir un aviso
        y aun no lo ha atendido, no se le manda otro; pasada la ventana, el
        carril vuelve a avisar aunque la cola siga sucia.

        Un fallo aqui no puede impedir que se avise: si la consulta falla, se
        responde que NO hay pendientes, porque quedarse callado ante un vehiculo
        sin identificar es peor que mandar un aviso de mas.
        """
        from datetime import datetime, timedelta, timezone

        from app.core.db import SessionLocal
        from app.models import AccessEvent, Decision

        window = float(cfg.get("pending_window", 90.0))
        if window <= 0:
            return False
        since = datetime.now(timezone.utc) - timedelta(seconds=window)
        db = SessionLocal()
        try:
            from sqlalchemy import select

            stmt = (
                select(AccessEvent.id)
                .where(
                    AccessEvent.decision == Decision.MANUAL_REVIEW,
                    AccessEvent.resolved_at.is_(None),
                    AccessEvent.occurred_at >= since,
                )
                .limit(1)
            )
            if cfg.get("lane_id"):
                stmt = stmt.where(AccessEvent.lane_id == cfg["lane_id"])
            return db.scalar(stmt) is not None
        except Exception:
            logger.debug("No se pudo consultar pendientes", exc_info=True)
            return False
        finally:
            db.close()

    def _majority_reading(self, now: float, cfg: dict) -> tuple:
        """La lectura mas repetida de la ventana y que proporcion represento.

        Las lecturas de una placa dificil salen casi todas distintas, asi que
        ninguna confirma. Pero la que mas se repite sigue siendo la mejor pista
        que tiene el sistema, y la proporcion dice cuanto fiarse de ella.
        """
        window = max(1.0, float(cfg.get("confirm_window", 6.0)))
        recent = [
            (text, at) for text, at in self._recent_reads if now - at <= window
        ]
        self._recent_reads = recent
        if not recent:
            return None, None, 0
        from collections import Counter

        counts = Counter(text for text, _ in recent)
        text, n = counts.most_common(1)[0]
        return text, round(n / len(recent), 2), len(recent)

    def _publish_overlay(
        self,
        frame: np.ndarray,
        vehicles: Optional[list],
        plates: Optional[list],
        reading: Optional[tuple[str, float]],
    ) -> None:
        """Deja la geometria lista para que la consola la dibuje.

        `vehicles=None` conserva las cajas de vehiculo anteriores: el detector
        de vehiculos corre una de cada N veces, y borrarlas en las vueltas
        intermedias haria parpadear el recuadro.
        """
        h, w = frame.shape[:2]
        s = self.status
        s.overlay_w, s.overlay_h = int(w), int(h)
        s.overlay_at = time.time()
        if vehicles is not None:
            s.overlay_vehicles = [
                {
                    "x1": round(b.x1, 1), "y1": round(b.y1, 1),
                    "x2": round(b.x2, 1), "y2": round(b.y2, 1),
                    "conf": round(float(b.confidence), 3),
                    "label": b.class_name,
                }
                for b in vehicles
            ]
        if plates is not None:
            text, text_conf = reading if reading else (None, None)
            s.overlay_plates = [
                {
                    "x1": round(b.x1, 1), "y1": round(b.y1, 1),
                    "x2": round(b.x2, 1), "y2": round(b.y2, 1),
                    "conf": round(float(b.confidence), 3),
                    # Solo la caja mas grande lleva el texto: es la que el
                    # lector intento primero.
                    "text": text if i == 0 else None,
                    "text_conf": round(text_conf, 3) if (text_conf and i == 0) else None,
                }
                for i, b in enumerate(plates[:3])
            ]

    def _record_time(self, elapsed_ms: float) -> None:
        s = self.status
        s._times.append(elapsed_ms)
        if len(s._times) > 30:
            del s._times[:-30]
        s.avg_inference_ms = round(sum(s._times) / len(s._times), 1)

    def _set(self, state: str, detail: str) -> None:
        self.status.state = state
        self.status.detail = detail


_watcher: Optional[LaneWatcher] = None
_watcher_lock = threading.Lock()


def get_watcher() -> LaneWatcher:
    global _watcher
    with _watcher_lock:
        if _watcher is None:
            _watcher = LaneWatcher()
        return _watcher
