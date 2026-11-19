"""Vista en vivo de la cámara para la consola de caseta.

PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md

El navegador no habla RTSPS y la URL del flujo lleva un token. Así que el
backend se conecta a la cámara (una sola vez, ver `hardware/camera_stream.py`)
y reparte fotogramas JPEG por estas rutas, protegidas por la misma cookie de
sesión que todo lo demás.

Control de acceso: `Operators` — administrador, supervisor y guardia. El
auditor es un rol de sólo lectura sobre el registro, y darle el vídeo en vivo
de una caseta sería ampliarle el alcance sin que nadie lo haya decidido.

La URL del flujo **nunca** sale por estas rutas: `/status` devuelve estado,
nombre y métricas, nunca la cadena de conexión.
"""
import asyncio
from typing import AsyncIterator

from fastapi import APIRouter, HTTPException, Request, Response, status
from fastapi.responses import StreamingResponse

from app.api.deps import AdminOnly, DbSession, Operators
from sqlalchemy import select

from app.hardware.camera_stream import CameraStream, get_stream
from app.schemas.operations import GateCalibrateIn, GateJogIn, GateOpenIn

router = APIRouter(prefix="/camera", tags=["camera"])

BOUNDARY = "scaeframe"
# Intervalo de sondeo del generador. Pone un TECHO a los fps que puede
# entregar: a 0.1 s eran 10 fps como mucho, por debajo de lo que la camara
# puede dar. A 0.02 s el techo sube a 50 fps, muy por encima de los 24 de la
# fuente, asi que deja de ser el factor limitante. El coste es una espera de
# asyncio por espectador, despreciable frente a decodificar video.
POLL_SECONDS = 0.02
# Sin imagen durante este tiempo se cierra la respuesta: el cliente reintenta y
# el servidor no se queda con un espectador que ya no existe.
STARVE_SECONDS = 15.0

# Una vista en vivo no se cachea nunca, ni en el navegador ni en un proxy.
NO_STORE = {
    "Cache-Control": "no-store, no-cache, must-revalidate, private",
    "Pragma": "no-cache",
    "Expires": "0",
    "X-Accel-Buffering": "no",
}


@router.get("/status")
def camera_status(user: Operators):
    """Estado de la captura, para que la consola pueda mostrarlo."""
    s = get_stream()
    st = s.status
    return {
        "configured": s.configured,
        "name": s.camera_name,
        "state": st.state if s.configured else "not_configured",
        "detail": st.detail if s.configured else "No hay cámara configurada.",
        "viewers": st.viewers,
        "frames": st.frames,
        "fps": st.fps,
        "width": st.width,
        "height": st.height,
        "reconnects": st.reconnects,
        "last_frame_at": st.last_frame_at,
    }


@router.get("/snapshot")
def camera_snapshot(user: Operators):
    """Un solo fotograma. Útil como vista previa ligera y para diagnosticar
    sin abrir un flujo continuo."""
    s = get_stream()
    if not s.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No hay cámara configurada."
        )
    s.acquire()
    try:
        frame = s.latest(timeout=15.0)
    finally:
        s.release()
    if frame is None:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            f"La cámara no entregó imagen: {s.status.detail or s.status.state}",
        )
    return Response(content=frame, media_type="image/jpeg", headers=NO_STORE)


async def _mjpeg(request: Request, s: CameraStream) -> AsyncIterator[bytes]:
    """Generador MJPEG asíncrono.

    Es asíncrono por una razón concreta, encontrada probando: un generador
    síncrono corre en el threadpool y, mientras está bloqueado esperando un
    fotograma, Starlette no puede cerrarlo ni preguntar si el cliente sigue
    ahí. Al cortar tres clientes a la vez, los tres espectadores quedaban
    contados para siempre, el hilo de captura nunca veía cero y FFmpeg seguía
    decodificando 4K para nadie. Aquí se sondea el último fotograma sin
    bloquear y se consulta `is_disconnected()` en cada vuelta, que es la forma
    fiable de saber que el guardia cerró la pestaña.
    """
    s.acquire()
    last_seq = -1
    idle = 0.0
    try:
        while True:
            if await request.is_disconnected():
                return
            seq, frame = s.peek()
            if frame is not None and seq != last_seq:
                last_seq = seq
                idle = 0.0
                yield (
                    f"--{BOUNDARY}\r\n"
                    f"Content-Type: image/jpeg\r\n"
                    f"Content-Length: {len(frame)}\r\n\r\n"
                ).encode() + frame + b"\r\n"
            else:
                idle += POLL_SECONDS
                if idle >= STARVE_SECONDS:
                    return
            await asyncio.sleep(POLL_SECONDS)
    finally:
        s.release()


@router.get("/stream")
async def camera_stream(request: Request, user: Operators):
    """MJPEG (`multipart/x-mixed-replace`), que un `<img>` pinta sin librerías.

    Se eligió MJPEG sobre WebRTC y HLS: no necesita negociación ni dependencias
    en el navegador, y no arrastra los segundos de latencia de HLS, que en una
    caseta importan. El coste es más ancho de banda, irrelevante en LAN.
    """
    s = get_stream()
    if not s.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No hay cámara configurada."
        )
    return StreamingResponse(
        _mjpeg(request, s),
        media_type=f"multipart/x-mixed-replace; boundary={BOUNDARY}",
        headers=NO_STORE,
    )


# ---------------------------------------------------------------------------
# Reconocimiento automatico sobre el video en vivo
# PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md
#
# El vigilante de carril comparte el proceso de captura de la vista en vivo: no
# abre una segunda conexion RTSPS, porque decodificar dos veces el mismo video
# en esta Pi significaria quitarle ~35 % de un nucleo a la plataforma de la
# tesis para nada.
# ---------------------------------------------------------------------------
@router.get("/recognition")
def recognition_status(user: Operators):
    """Estado del reconocimiento automatico."""
    from app.recognition.lane_watcher import get_watcher

    w = get_watcher()
    s = w.status
    return {
        "enabled": s.enabled,
        "running": w.running,
        "state": s.state,
        "detail": s.detail,
        "frames_seen": s.frames_seen,
        "frames_analysed": s.frames_analysed,
        "plates_detected": s.plates_detected,
        "plates_read": s.plates_read,
        "plates_unreadable": s.plates_unreadable,
        "plates_too_small": s.plates_too_small,
        "last_guess": s.last_guess,
        "last_consistency": s.last_consistency,
        "vehicles_seen": s.vehicles_seen,
        "events_created": s.events_created,
        "suppressed": s.suppressed,
        "last_plate": s.last_plate,
        "last_confidence": s.last_confidence,
        "last_decision": s.last_decision,
        "last_event_id": s.last_event_id,
        "last_at": s.last_at,
        "avg_inference_ms": s.avg_inference_ms,
    }


# ---------------------------------------------------------------------------
# Calibracion del servo de la barrera
# PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md
#
# La calibracion va POR AQUI, y no por un script suelto, por una razon dura: el
# proceso del servicio tiene la linea GPIO17 reclamada mientras vive, y dos
# procesos no pueden reclamarla a la vez. Un script externo fallaria con la
# linea ocupada, o -peor- obligaria a parar el servicio y dejaria la caseta a
# ciegas para mover un servo.
#
# Solo administrador: mover hierro para encontrar los topes de un servo es
# mantenimiento, no operacion de caseta.
# ---------------------------------------------------------------------------
def _sweep_degrees(cfg) -> float:
    """Recorrido entre cerrado y abierto, en grados aproximados.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Se reparte sobre el margen util REAL del servo, no sobre los 2000 us del
    margen estandar. Daba un numero distinto al de `jog`, que si usaba el margen
    real: el mismo recorrido salia como "90 grados" en la herramienta de
    calibracion y como "103.5 grados" al guardarlo. Dos cuentas para lo mismo es
    peor que ninguna, porque invita a creer la equivocada.
    """
    span = max(1, cfg.pulse_max_us - cfg.pulse_min_us)
    return round(abs(cfg.open_us - cfg.closed_us) / span * 180, 1)


@router.get("/gate")
def gate_status(user: AdminOnly, db: DbSession):
    """Estado del servo y los anchos de pulso configurados."""
    from app.core.config import get_settings
    from app.hardware.servo_gate import (
        HARD_MAX_PULSE_US, HARD_MIN_PULSE_US, ServoConfig, known_gates,
    )
    from app.models import BarrierController

    rows = db.scalars(
        select(BarrierController).where(BarrierController.driver == "servo")
    ).all()
    controllers = []
    for row in rows:
        cfg = ServoConfig.from_row(row.config)
        controllers.append({
            "id": row.id, "lane_id": row.lane_id, "name": row.name,
            "chip": cfg.chip, "pin": cfg.pin,
            "closed_us": cfg.closed_us, "open_us": cfg.open_us,
            "travel_ms": cfg.travel_ms, "open_seconds": cfg.open_seconds,
            "pulse_min_us": cfg.pulse_min_us, "pulse_max_us": cfg.pulse_max_us,
            "sweep_degrees": _sweep_degrees(cfg),
        })
    return {
        "enabled": get_settings().gate_enabled,
        "controllers": controllers,
        "live": known_gates(),
        # Topes duros del driver, por encima del margen util de cada servo.
        "limits": {
            "hard_min_pulse_us": HARD_MIN_PULSE_US,
            "hard_max_pulse_us": HARD_MAX_PULSE_US,
        },
    }


@router.post("/gate/open")
def gate_open(payload: GateOpenIn, request: Request, user: Operators, db: DbSession):
    """Abrir la pluma a mano desde la consola.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Disponible para los **operadores** (administrador, supervisor y guardia), no
    solo para el administrador: abrir la pluma a un peaton, a una ambulancia o a
    un coche cuya placa la camara no ve es trabajo de caseta, no mantenimiento.
    El `jog` de calibracion si se queda en administrador, porque eso es buscar
    topes mecanicos.

    Cada apertura deja un evento firmado por quien la pidio. Ver
    `access.open_barrier_manually` para por que un evento y no solo auditoria.
    """
    from app.core.config import get_settings
    from app.models import Lane, Role
    from app.services import access, audit

    if not get_settings().gate_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "La barrera esta deshabilitada por configuracion "
            "(SCAE_GATE_ENABLED=false).",
        )

    target = payload.lane_id or user.default_lane_id
    if not target:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "No tienes un carril asignado. Pide al administrador que te asigne uno.",
        )
    lane = db.get(Lane, target)
    if lane is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El carril no existe.")
    if user.role is Role.GUARD and user.default_lane_id and lane.id != user.default_lane_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo puedes abrir la barrera del carril que tienes asignado.",
        )

    event = access.open_barrier_manually(
        db, lane=lane, actor=user, notes=payload.notes,
    )
    audit.record(
        db, action="gate.manual_open", actor=user, entity_type="access_event",
        entity_id=event.id,
        summary=f"Apertura manual en {lane.code}: {event.actuation_status.value}",
        payload={"actuation_detail": event.actuation_detail,
                 "notes": payload.notes},
        request=request,
    )
    db.commit()
    db.refresh(event)
    return {
        "event_id": event.id,
        "lane": lane.code,
        "actuation_status": event.actuation_status.value,
        "actuation_detail": event.actuation_detail,
        "occurred_at": event.occurred_at.isoformat(),
    }


@router.post("/gate/jog")
def gate_jog(payload: GateJogIn, request: Request, user: AdminOnly, db: DbSession):
    """Mueve el servo a un ancho de pulso concreto, para localizar los topes.

    No abre la barrera: no marca estado, no programa cierre y no cuenta como
    apertura. Cada movimiento queda en la auditoria, porque mueve hardware.
    """
    from app.core.config import get_settings
    from app.hardware.servo_gate import (
        ServoConfig, ServoUnavailable, get_gate,
    )
    from app.models import BarrierController
    from app.services import audit

    if not get_settings().gate_enabled:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "El servo esta deshabilitado (SCAE_GATE_ENABLED=false).",
        )
    row = db.get(BarrierController, payload.controller_id)
    if row is None or row.driver != "servo":
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No hay un controlador de servo con ese id.")
    cfg = ServoConfig.from_row(row.config)
    try:
        result = get_gate(cfg).jog(payload.pulse_us, payload.hold_ms)
    except ServoUnavailable as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc

    audit.record(
        db, action="gate.jog", actor=user, entity_type="barrier_controller",
        entity_id=row.id,
        summary=f"Calibracion: pulso {result['pulse_us']} us "
                f"(~{result['approx_degrees']} grados)",
        payload=result, request=request,
    )
    db.commit()
    return result


@router.post("/gate/calibrate")
def gate_calibrate(
    payload: GateCalibrateIn, request: Request, user: AdminOnly, db: DbSession,
):
    """Guarda las posiciones de cerrado y abierto en el controlador."""
    from app.hardware.servo_gate import ServoConfig, _gates, _gates_lock
    from app.models import BarrierController
    from app.services import audit

    row = db.get(BarrierController, payload.controller_id)
    if row is None or row.driver != "servo":
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "No hay un controlador de servo con ese id.")
    if abs(payload.open_us - payload.closed_us) < 100:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Las posiciones de apertura y cierre estan demasiado juntas: "
            "la barrera no se moveria lo suficiente para notarse.",
        )

    before = dict(row.config or {})
    new_config = {**before, "closed_us": payload.closed_us,
                  "open_us": payload.open_us}
    if payload.travel_ms is not None:
        new_config["travel_ms"] = payload.travel_ms
    if payload.open_seconds is not None:
        new_config["open_seconds"] = payload.open_seconds
    if payload.pulse_min_us is not None:
        new_config["pulse_min_us"] = payload.pulse_min_us
    if payload.pulse_max_us is not None:
        new_config["pulse_max_us"] = payload.pulse_max_us
    # SQLAlchemy no detecta la mutacion de un dict JSON en sitio.
    row.config = new_config

    # La instancia viva guarda su propia ServoConfig, asi que hay que avisarla
    # o seguiria pulsando con los anchos antiguos hasta reiniciar el servicio.
    # Se actualiza EN SITIO, sin soltar la linea: tirarla y dejar que naciera
    # otra provocaba un 'GPIO busy' en la siguiente apertura real.
    cfg = ServoConfig.from_row(new_config)
    with _gates_lock:
        live = _gates.get((cfg.chip, cfg.pin))
    if live is not None:
        live.update_config(cfg)

    audit.record(
        db, action="gate.calibrate", actor=user,
        entity_type="barrier_controller", entity_id=row.id,
        summary=f"Cerrado {payload.closed_us} us / abierto {payload.open_us} us",
        payload={"before": before, "after": new_config}, request=request,
    )
    db.commit()
    # Aviso si alguna posicion queda cerca de los extremos.
    #
    # No se prohibe -hay servos que llegan- pero conviene decirlo: un servo
    # mandado repetidamente contra su tope mecanico no se para, sigue
    # empujando, y lo que cede son los engranajes de plastico. El sintoma luego
    # es desconcertante: consume corriente (se ve en las caidas de tension),
    # no mueve el brazo, y **no se calienta**, porque el motor gira libre en
    # vez de calarse. Paso aqui con un cierre calibrado a 650 us.
    SAFE_LO, SAFE_HI = 1000, 2000
    warnings = [
        f"{label} en {value} us queda fuera del margen conservador "
        f"{SAFE_LO}-{SAFE_HI} us. Si el servo no llega hasta ahi, cada "
        f"movimiento lo empuja contra su tope y acaba pelando los engranajes."
        for label, value in (("cerrado", cfg.closed_us), ("abierto", cfg.open_us))
        if value < SAFE_LO or value > SAFE_HI
    ]
    return {
        "controller_id": row.id,
        "closed_us": cfg.closed_us, "open_us": cfg.open_us,
        "travel_ms": cfg.travel_ms, "open_seconds": cfg.open_seconds,
        "sweep_degrees": _sweep_degrees(cfg),
        "warnings": warnings,
    }


@router.post("/capture-now")
def capture_now(request: Request, user: Operators, db: DbSession,
                lane_id: int | None = None):
    """Analizar el carril AHORA y meter el resultado en el pipeline.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Es el mismo camino que recorre una captura automatica -mismo fotograma,
    mismo detector, mismo OCR, mismo motor de decision, misma barrera- pero
    disparado a mano por el guardia en vez de por el vigilante.

    Existe por dos razones distintas, y las dos pesan:

    * **Operativa.** El vigilante decide cuando mirar: puerta de movimiento,
      esperar a que el carril se despeje, exigir alguna lectura. Todo eso esta
      bien para que no inunde la consola, pero deja al guardia sin forma de
      decir "mira ahora" cuando tiene un coche delante y el sistema calla.
    * **Para probar.** Permite ejercitar el pipeline completo sin pelearse con
      esas puertas, que es justo lo que costaba durante la puesta en marcha.

    A diferencia del automatico, **esto siempre responde**: el guardia pulso un
    boton y merece una respuesta, aunque no se lea ninguna placa. En ese caso el
    evento sale como MANUAL_REVIEW sin placa y la consola le abre el alta con el
    campo vacio para que la teclee. El automatico se calla en ese caso a
    proposito (ver `_no_identification`); aqui hay intencion explicita.

    **No es una simulacion.** Si la placa esta en el padron y la lectura es
    buena, esto abre la barrera de verdad, igual que si hubiera llegado el coche.
    """
    from app.api.serializers import event_detail
    from app.models import EventSource, Lane, Role
    from app.recognition.pipeline import decode_image, get_pipeline
    from app.services import access, audit

    s = get_stream()
    if not s.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No hay camara configurada."
        )

    target = lane_id or user.default_lane_id
    lane = db.get(Lane, target) if target else None
    if target and lane is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El carril no existe.")
    if (
        lane is not None
        and user.role is Role.GUARD
        and user.default_lane_id
        and lane.id != user.default_lane_id
    ):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Solo puedes capturar en el carril que tienes asignado.",
        )

    # Se cuenta como espectador el tiempo justo: si nadie mas esta mirando, la
    # captura compartida no deberia quedarse abierta por una pulsacion.
    s.acquire()
    try:
        jpeg = s.latest(timeout=15.0)
    finally:
        s.release()
    if jpeg is None:
        raise HTTPException(
            status.HTTP_504_GATEWAY_TIMEOUT,
            f"La camara no entrego imagen: {s.status.detail or s.status.state}",
        )

    # Se analiza aqui y se le pasa hecho a process_capture, que si no volveria a
    # ejecutar detector y OCR sobre el mismo JPEG (ver parche 24).
    precomputed = None
    frame = decode_image(jpeg)
    if frame is not None:
        import time as _t

        from app.recognition.pipeline import RecognitionResult
        from app.recognition.plate import best_reading

        pipe = get_pipeline()
        t0 = _t.perf_counter()
        boxes = pipe.detector.detect(frame)
        fragments: list = []
        for box in boxes[:3]:
            fragments.extend(pipe.reader.read(box.crop(frame)))
            if best_reading(fragments).is_valid:
                break
        precomputed = RecognitionResult(
            reading=best_reading(fragments) if fragments else None,
            boxes=list(boxes),
            detector_name=getattr(pipe.detector, "name", ""),
            ocr_name=getattr(pipe.reader, "name", ""),
            elapsed_ms=int((_t.perf_counter() - t0) * 1000),
        )

    camera = None
    if lane is not None:
        camera = next((c for c in lane.cameras if c.is_active), None)

    event = access.process_capture(
        db, image_bytes=jpeg, lane=lane, actor=user,
        source=EventSource.CAMERA, camera=camera, precomputed=precomputed,
        notes="Captura forzada por el guardia desde la consola.",
    )
    audit.record(
        db, action="camera.capture_now", actor=user,
        entity_type="access_event", entity_id=event.id,
        summary=f"{event.decision.value} - {event.plate_normalized or 'sin placa'}",
        request=request,
    )
    db.commit()
    db.refresh(event)

    from app.api.routes.events import _capture_context

    return _capture_context(db, event)


@router.get("/overlay")
def recognition_overlay(user: Operators, max_age: float = 3.0):
    """Geometria de lo ultimo detectado, para dibujarla sobre el video.

    Se sirve aparte del MJPEG a proposito. Quemar los rectangulos en el JPEG
    obligaria a decodificar, dibujar y recodificar cada fotograma en el
    servidor -en una Pi que comparte CPU con la tesis- y ademas ataria el
    overlay a los 24 fps del video. Mandando solo las coordenadas, el navegador
    dibuja un SVG encima del `<img>`: coste de servidor practicamente nulo y el
    guardia puede ver la caja aunque el analisis vaya a 2 fps.

    `max_age` descarta geometria vieja: sin eso, al salir el coche del carril
    quedaria un rectangulo flotando sobre el asfalto vacio.
    """
    import time as _t

    from app.recognition.lane_watcher import get_watcher

    s = get_watcher().status
    fresh = s.overlay_at > 0 and (_t.time() - s.overlay_at) <= max_age
    return {
        "at": s.overlay_at or None,
        "age_s": round(_t.time() - s.overlay_at, 2) if s.overlay_at else None,
        "fresh": fresh,
        "frame_width": s.overlay_w or None,
        "frame_height": s.overlay_h or None,
        "vehicles": s.overlay_vehicles if fresh else [],
        "plates": s.overlay_plates if fresh else [],
        "running": get_watcher().running,
        "inference_ms": s.avg_inference_ms,
    }


@router.post("/recognition/start")
def recognition_start(request: Request, user: Operators, db: DbSession):
    """Arranca el procesamiento. No toca la vista en vivo."""
    from app.recognition.lane_watcher import get_watcher
    from app.services import audit

    s = get_stream()
    if not s.configured:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE, "No hay camara configurada."
        )
    started = get_watcher().start()
    audit.record(
        db, action="camera.recognition_started", actor=user,
        summary="Reconocimiento automatico activado", request=request,
    )
    db.commit()
    return {"started": started, "running": get_watcher().running}


@router.post("/recognition/stop")
def recognition_stop(request: Request, user: Operators, db: DbSession):
    """Detiene el procesamiento sin cortar el video en vivo."""
    from app.recognition.lane_watcher import get_watcher
    from app.services import audit

    stopped = get_watcher().stop()
    audit.record(
        db, action="camera.recognition_stopped", actor=user,
        summary="Reconocimiento automatico detenido", request=request,
    )
    db.commit()
    return {"stopped": stopped, "running": get_watcher().running}
