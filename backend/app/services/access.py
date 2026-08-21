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
) -> AccessEvent:
    """Run one access attempt end to end and persist it as a single event."""
    now = datetime.now(timezone.utc)
    recognition: Optional[RecognitionResult] = None
    reading: Optional[PlateReading] = None
    evidence_path: Optional[str] = None

    if typed_plate:
        # Manual entry still goes through normalization so the key matches.
        reading = interpret(typed_plate, ocr_confidence=1.0)
    elif image_bytes:
        evidence_path = store_evidence(image_bytes)
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


def label_event(event: AccessEvent) -> dict[str, str]:
    return {
        "decision_label": DECISION_LABELS.get(event.decision, event.decision.value),
        "reason_label": REASON_LABELS.get(event.decision_reason, event.decision_reason.value),
        "plate_display": format_for_display(event.plate_normalized) if event.plate_normalized else None,
    }
