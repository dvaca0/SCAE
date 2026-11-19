"""Orchestration of the access flow.

    capture -> recognize -> normalize -> resolve identity -> decide
            -> actuate -> record event -> audit

Each arrow is a separate call here, so any of them can be replaced (a real
camera, a better OCR, a physical barrier) without the others noticing.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.hardware.drivers import (
    ActuationResult, DriverNotAvailable, get_barrier_driver,
)
from app.models import (
    AccessEvent, ActuationStatus, Authorization, AuthorizationStatus, Camera,
    DataOrigin, Decision, DecisionReason, Detection, EventSource, Incident,
    IncidentSeverity, IncidentStatus, IncidentType, Lane, Person, User,
    Vehicle, WatchlistEntry,
)
from app.recognition.pipeline import RecognitionResult, decode_image, get_pipeline
from app.recognition.plate import PlateReading, format_for_display, interpret
from app.services.decision import (
    AuthorizationView, DecisionRequest, DecisionResult, evaluate,
)

logger = logging.getLogger(__name__)
settings = get_settings()

DECISION_LABELS = {
    Decision.ALLOW: "Acceso permitido",
    Decision.DENY: "Acceso denegado",
    Decision.MANUAL_REVIEW: "Revisión manual",
}

REASON_LABELS = {
    DecisionReason.AUTHORIZED: "Permiso vigente",
    DecisionReason.NO_PLATE_READ: "Sin lectura de placa",
    DecisionReason.LOW_CONFIDENCE: "Lectura de baja confianza",
    DecisionReason.UNKNOWN_VEHICLE: "Vehículo no registrado",
    DecisionReason.NO_AUTHORIZATION: "Sin permiso",
    DecisionReason.AUTHORIZATION_SUSPENDED: "Permiso suspendido",
    DecisionReason.AUTHORIZATION_EXPIRED: "Permiso expirado",
    DecisionReason.OUT_OF_SCHEDULE: "Fuera de horario",
    DecisionReason.WRONG_ACCESS_POINT: "Acceso no cubierto por el permiso",
    DecisionReason.WATCHLISTED: "Placa en lista de control",
    DecisionReason.MANUAL_OVERRIDE: "Decisión manual del guardia",
}


# --------------------------------------------------------------------------
# Identity resolution
# --------------------------------------------------------------------------
def resolve_vehicle(db: Session, plate: Optional[str]) -> Optional[Vehicle]:
    if not plate:
        return None
    return db.scalar(select(Vehicle).where(Vehicle.plate == plate, Vehicle.is_active.is_(True)))


def _edit_distance(a: str, b: str, cap: int = 3) -> int:
    """Distancia de edicion, con corte temprano.

    `cap` evita recorrer la matriz entera cuando ya se sabe que las cadenas
    estan demasiado lejos: aqui solo interesan las placas que se parecen mucho.
    """
    if a == b:
        return 0
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        current = [i]
        for j, cb in enumerate(b, 1):
            current.append(min(
                previous[j] + 1,            # borrar
                current[j - 1] + 1,         # insertar
                previous[j - 1] + (ca != cb),  # sustituir
            ))
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


def similar_vehicles(
    db: Session, plate: Optional[str], *, max_distance: int = 2, limit: int = 4,
) -> list[tuple["Vehicle", int]]:
    """Vehiculos del padron cuya placa se parece a `plate`.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Existe por un problema real y medido: con una placa de poco contraste el OCR
    lee algo distinto cada vez -FTC224A, FTC324A, VTC324A, FTG224A, FTC274A...
    del MISMO coche- asi que ninguna lectura coincide con lo registrado y el
    guardia acaba dando de alta a la misma persona una y otra vez. El padron de
    esta maqueta llego a tener siete vehiculos que eran uno solo.

    **Esto NO concede acceso.** Un parecido no es una identificacion: dos placas
    reales pueden diferir en un caracter y pertenecer a dos coches distintos.
    Lo unico que hace es ponerle al guardia delante "esto se parece a lo que ya
    tienes registrado", para que decida el, y para que no duplique la ficha.

    Se compara sobre la forma normalizada, que es la que se usa para buscar.
    """
    if not plate:
        return []
    rows = db.scalars(
        select(Vehicle).where(Vehicle.is_active.is_(True), Vehicle.plate != plate)
    ).all()
    scored = []
    for vehicle in rows:
        d = _edit_distance(plate, vehicle.plate, cap=max_distance)
        if d <= max_distance:
            scored.append((vehicle, d))
    # Primero las mas parecidas; a igual distancia, la mas reciente.
    scored.sort(key=lambda pair: (pair[1], -pair[0].id))
    return scored[:limit]


def active_watchlist_entry(db: Session, plate: Optional[str]) -> Optional[WatchlistEntry]:
    if not plate:
        return None
    return db.scalar(
        select(WatchlistEntry).where(
            WatchlistEntry.plate == plate, WatchlistEntry.is_active.is_(True)
        )
    )


def authorization_views(db: Session, vehicle_id: int) -> list[AuthorizationView]:
    rows = db.scalars(
        select(Authorization).where(Authorization.vehicle_id == vehicle_id)
    ).all()
    return [
        AuthorizationView(
            id=a.id, status=a.status, valid_from=a.valid_from,
            valid_until=a.valid_until, campus_id=a.campus_id,
            access_point_id=a.access_point_id, person_id=a.person_id,
        )
        for a in rows
    ]


# --------------------------------------------------------------------------
# Actuation
# --------------------------------------------------------------------------
def actuate(lane: Optional[Lane], result: DecisionResult) -> ActuationResult | None:
    """Ask the lane's controller to open. Returns None when no actuation is
    warranted, so 'not required' is never confused with 'failed'."""
    if lane is None or result.decision is not Decision.ALLOW:
        return None
    if not lane.auto_open_on_allow:
        return None
    controller = next((c for c in lane.controllers if c.is_active), None)
    if controller is None:
        return None
    try:
        driver = get_barrier_driver(controller.driver)
        return driver.open(controller.config or {}, result.reason.value)
    except DriverNotAvailable as exc:
        logger.warning("Actuación no disponible en carril %s: %s", lane.code, exc)
        return ActuationResult(False, simulated=False, detail=str(exc))


def _actuation_fields(actuation: ActuationResult | None) -> tuple[ActuationStatus, str | None]:
    if actuation is None:
        return ActuationStatus.NOT_REQUIRED, None
    if actuation.simulated:
        return ActuationStatus.SIMULATED, actuation.detail
    return (
        ActuationStatus.SUCCEEDED if actuation.succeeded else ActuationStatus.FAILED,
        actuation.detail,
    )


# --------------------------------------------------------------------------
# Evidence
# --------------------------------------------------------------------------
def store_evidence(data: bytes, suffix: str = ".jpg") -> str:
    """Write the frame under a random name.

    The filename is never derived from user input, so an upload cannot
    traverse out of the evidence directory.
    """
    now = datetime.now(timezone.utc)
    folder = settings.evidence_dir / f"{now:%Y/%m/%d}"
    folder.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}{suffix}"
    (folder / name).write_bytes(data)
    return str((folder / name).relative_to(settings.evidence_dir))


# --------------------------------------------------------------------------
# The main flow
# --------------------------------------------------------------------------
def process_capture(
    db: Session,
    *,
    image_bytes: Optional[bytes],
    lane: Optional[Lane],
    actor: Optional[User],
    source: EventSource = EventSource.MANUAL_UPLOAD,
    typed_plate: Optional[str] = None,
    camera: Optional[Camera] = None,
    notes: Optional[str] = None,
    precomputed: Optional[RecognitionResult] = None,
) -> AccessEvent:
    """Run one access attempt end to end and persist it as a single event.

    `precomputed` evita volver a mirar un fotograma que el llamante ya analizo.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md: el vigilante de
    carril ya ha ejecutado detector y OCR sobre este mismo JPEG para decidir que
    merecia un evento. Sin esto, `process_capture` los ejecutaba OTRA VEZ sobre
    la misma imagen: medido en el log, un segundo entero entre el momento en que
    el vigilante decide avisar y el momento en que el evento existe, con el coche
    parado delante de la pluma. Es el mismo doble pase que se corrigio dentro del
    vigilante (parche 18), escondido un nivel mas abajo.
    """
    now = datetime.now(timezone.utc)
    recognition: Optional[RecognitionResult] = None
    reading: Optional[PlateReading] = None
    evidence_path: Optional[str] = None

    if typed_plate:
        # Manual entry still goes through normalization so the key matches.
        reading = interpret(typed_plate, ocr_confidence=1.0)
    elif image_bytes:
        evidence_path = store_evidence(image_bytes)
        if precomputed is not None:
            # Ya analizado por el llamante: no se vuelve a inferir.
            recognition = precomputed
            reading = precomputed.reading
        else:
            frame = decode_image(image_bytes)
            if frame is None:
                reading = None
            else:
                recognition = get_pipeline().process(frame)
                reading = recognition.reading

    plate = reading.normalized if reading and reading.is_valid else None
    confidence = reading.confidence if reading else 0.0

    vehicle = resolve_vehicle(db, plate)
    watch = active_watchlist_entry(db, plate)

    access_point_id = lane.access_point_id if lane else None
    campus_id = lane.access_point.campus_id if lane and lane.access_point else None

    result = evaluate(
        DecisionRequest(
            plate=plate,
            confidence=confidence,
            at=now,
            campus_id=campus_id,
            access_point_id=access_point_id,
            vehicle_id=vehicle.id if vehicle else None,
            authorizations=authorization_views(db, vehicle.id) if vehicle else (),
            is_watchlisted=watch is not None,
            watchlist_reason=watch.reason if watch else None,
            review_threshold=settings.review_confidence_threshold,
        )
    )

    # --- PARCHE LOCAL (Raspberry Pi 5): suelo para mover hierro ----------
    # El motor de decision manda a revision por debajo de
    # `review_confidence_threshold` (0.55). Ese umbral decide "necesito que
    # mire un humano". Pero desde que la barrera es un servo real, un ALLOW
    # automatico ya no es una fila en una tabla: **mueve hardware**, y una
    # lectura del 60 % que casualmente coincide con una placa del padron no es
    # base suficiente para abrirle la puerta a un coche.
    #
    # Asi que una captura de camara, sin operador delante, exige ademas
    # `gate_auto_open_min_confidence` (0.80). Si no llega, el evento baja a
    # revision manual y lo resuelve el guardia; no se deniega, porque el
    # vehiculo puede estar perfectamente autorizado y el problema ser la foto.
    #
    # No se aplica a entradas manuales ni a subidas: ahi ya hay un humano
    # respondiendo por la decision.
    if (
        source is EventSource.CAMERA
        and result.decision is Decision.ALLOW
        and confidence < settings.gate_auto_open_min_confidence
    ):
        logger.info(
            "ALLOW automatico degradado a revision: confianza %.2f < %.2f",
            confidence, settings.gate_auto_open_min_confidence,
        )
        result = DecisionResult(
            Decision.MANUAL_REVIEW,
            DecisionReason.LOW_CONFIDENCE,
            authorization_id=result.authorization_id,
            person_id=result.person_id,
            detail=(
                f"Lectura al {confidence:.0%}, por debajo del "
                f"{settings.gate_auto_open_min_confidence:.0%} que se exige "
                "para abrir la barrera sin que intervenga un guardia."
            ),
        )

    actuation = actuate(lane, result)
    actuation_status, actuation_detail = _actuation_fields(actuation)

    person_id = result.person_id or (vehicle.owner_id if vehicle else None)

    event = AccessEvent(
        occurred_at=now,
        lane_id=lane.id if lane else None,
        camera_id=camera.id if camera else None,
        source=source,
        plate_read=(reading.raw if reading else None) or typed_plate,
        plate_normalized=plate,
        plate_confidence=confidence or None,
        vehicle_id=vehicle.id if vehicle else None,
        person_id=person_id,
        authorization_id=result.authorization_id,
        decision=result.decision,
        decision_reason=result.reason,
        decided_by_id=actor.id if actor and typed_plate else None,
        is_manual=bool(typed_plate),
        notes=notes or result.detail,
        actuation_status=actuation_status,
        actuation_detail=actuation_detail,
        evidence_path=evidence_path,
        processing_ms=recognition.elapsed_ms if recognition else None,
        origin=DataOrigin.REAL,
    )
    db.add(event)
    db.flush()

    if recognition:
        _persist_detections(db, event, recognition)

    if result.decision is Decision.ALLOW or result.decision is Decision.DENY:
        event.resolved_at = now

    _maybe_open_incident(db, event, result, watch, actor)
    return event


def _persist_detections(db: Session, event: AccessEvent, rec: RecognitionResult) -> None:
    candidates = rec.reading.candidates if rec.reading else []
    if rec.boxes:
        for box in rec.boxes[:5]:
            db.add(Detection(
                event_id=event.id, detector=rec.detector_name,
                object_class=box.class_name, object_confidence=box.confidence,
                bbox_x1=box.x1, bbox_y1=box.y1, bbox_x2=box.x2, bbox_y2=box.y2,
                ocr_engine=rec.ocr_name, ocr_candidates=candidates,
            ))
    else:
        # Still record the attempt, so "no vehicle found" is visible later.
        db.add(Detection(
            event_id=event.id, detector=rec.detector_name, object_class=None,
            ocr_engine=rec.ocr_name, ocr_candidates=candidates,
        ))


def _maybe_open_incident(
    db: Session, event: AccessEvent, result: DecisionResult,
    watch: Optional[WatchlistEntry], actor: Optional[User],
) -> None:
    if not result.requires_incident:
        return
    if result.reason is DecisionReason.WATCHLISTED:
        kind, severity = IncidentType.WATCHLIST_HIT, (watch.severity if watch else IncidentSeverity.HIGH)
        title = f"Placa en lista de control: {event.plate_normalized}"
    else:
        kind, severity = IncidentType.UNAUTHORIZED_ATTEMPT, IncidentSeverity.MEDIUM
        title = f"Intento sin permiso vigente: {event.plate_normalized}"

    db.add(Incident(
        incident_type=kind, severity=severity, status=IncidentStatus.OPEN,
        title=title, description=result.detail, event_id=event.id,
        vehicle_id=event.vehicle_id, person_id=event.person_id,
        lane_id=event.lane_id, reported_by_id=actor.id if actor else None,
        origin=DataOrigin.REAL,
    ))


def apply_manual_decision(
    db: Session, event: AccessEvent, *, decision: Decision, actor: User,
    notes: Optional[str] = None, corrected_plate: Optional[str] = None,
    open_barrier: bool = True, lane: Optional[Lane] = None,
) -> AccessEvent:
    """A human resolves or overrides an event.

    The original automatic decision is preserved in the audit trail; the
    event itself records that a person decided and who.
    """
    if corrected_plate:
        reading = interpret(corrected_plate, ocr_confidence=1.0)
        if reading.is_valid:
            event.plate_normalized = reading.normalized
            event.plate_confidence = 1.0
            vehicle = resolve_vehicle(db, reading.normalized)
            if vehicle:
                event.vehicle_id = vehicle.id
                event.person_id = vehicle.owner_id

    event.decision = decision
    event.decision_reason = DecisionReason.MANUAL_OVERRIDE
    event.is_manual = True
    event.decided_by_id = actor.id
    event.resolved_at = datetime.now(timezone.utc)
    if notes:
        event.notes = notes

    if decision is Decision.ALLOW and open_barrier:
        actuation = actuate(lane, DecisionResult(Decision.ALLOW, DecisionReason.MANUAL_OVERRIDE))
        status, detail = _actuation_fields(actuation)
        event.actuation_status = status
        event.actuation_detail = detail

    return event


def open_barrier_manually(
    db: Session, *, lane: Optional[Lane], actor: User, notes: Optional[str] = None,
) -> AccessEvent:
    """El guardia abre la pluma a mano, sin que haya una placa de por medio.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Hace falta de verdad: alguien a pie, un vehiculo de emergencia, una placa
    que la camara no ve, una averia. Hasta ahora la unica forma de mover la
    pluma era resolver un evento, y si no habia evento no habia forma.

    **Deja un evento, no solo una linea de auditoria.** En un sistema de control
    de acceso, una barrera que se abre sin dejar registro es un agujero: al
    revisar el dia, la pluma habria subido y no habria constancia de quien ni
    por que. El evento va sin placa (no la hay), como ALLOW por decision manual
    y firmado por el guardia, que es exactamente lo que ocurrio.

    No pasa por `actuate()` a proposito: esa funcion respeta
    `lane.auto_open_on_allow`, pensado para decidir si una APERTURA AUTOMATICA
    esta permitida. Aqui hay un humano pulsando un boton, asi que esa bandera no
    deberia poder vetarlo.
    """
    now = datetime.now(timezone.utc)
    actuation: ActuationResult | None = None
    controller = None
    if lane is not None:
        controller = next((c for c in lane.controllers if c.is_active), None)

    if controller is None:
        actuation = ActuationResult(
            False, simulated=False,
            detail="El carril no tiene ningun controlador de barrera activo.",
        )
    else:
        try:
            driver = get_barrier_driver(controller.driver)
            actuation = driver.open(controller.config or {}, "apertura manual")
        except DriverNotAvailable as exc:
            logger.warning("Apertura manual no disponible en %s: %s", lane.code, exc)
            actuation = ActuationResult(False, simulated=False, detail=str(exc))

    status_value, detail = _actuation_fields(actuation)
    event = AccessEvent(
        occurred_at=now,
        lane_id=lane.id if lane else None,
        source=EventSource.MANUAL_ENTRY,
        plate_read=None, plate_normalized=None, plate_confidence=None,
        decision=Decision.ALLOW,
        decision_reason=DecisionReason.MANUAL_OVERRIDE,
        decided_by_id=actor.id,
        is_manual=True,
        resolved_at=now,
        notes=notes or "Apertura manual de la pluma desde la consola.",
        actuation_status=status_value,
        actuation_detail=detail,
        origin=DataOrigin.REAL,
    )
    db.add(event)
    db.flush()
    logger.info(
        "Apertura manual por %s en carril %s: %s",
        actor.username, lane.code if lane else "sin carril", status_value.value,
    )
    return event


def label_event(event: AccessEvent) -> dict[str, str]:
    return {
        "decision_label": DECISION_LABELS.get(event.decision, event.decision.value),
        "reason_label": REASON_LABELS.get(event.decision_reason, event.decision_reason.value),
        "plate_display": format_for_display(event.plate_normalized) if event.plate_normalized else None,
    }
