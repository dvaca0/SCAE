"""ORM -> schema helpers shared by the routers."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.hardware.drivers import (
    IMPLEMENTED_BARRIER_DRIVERS, IMPLEMENTED_CAMERA_DRIVERS,
)
from app.models import (
    AccessEvent, AccessPoint, Authorization, AuthorizationStatus, Campus,
    GuardShift, Incident, Lane, Person, User, Vehicle, WatchlistEntry,
)
from app.recognition.plate import format_for_display
from app.schemas.catalog import (
    AccessPointOut, AuthorizationOut, CampusOut, DeviceOut, LaneOut, PersonOut,
    VehicleOut, WatchlistOut,
)
from app.schemas.operations import (
    AccessEventDetail, AccessEventOut, AuditOut, DetectionOut, IncidentOut,
    ShiftOut,
)
from app.services.access import DECISION_LABELS, REASON_LABELS


def _aware(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- Infrastructure -------------------------------------------------------
def campus_out(db: Session, c: Campus) -> CampusOut:
    count = db.scalar(
        select(func.count(AccessPoint.id)).where(AccessPoint.campus_id == c.id)
    ) or 0
    return CampusOut.model_validate(c).model_copy(update={"access_point_count": count})


def access_point_out(db: Session, ap: AccessPoint) -> AccessPointOut:
    count = db.scalar(select(func.count(Lane.id)).where(Lane.access_point_id == ap.id)) or 0
    return AccessPointOut.model_validate(ap).model_copy(update={
        "campus_name": ap.campus.name if ap.campus else None,
        "lane_count": count,
    })


def lane_out(lane: Lane) -> LaneOut:
    ap = lane.access_point
    return LaneOut.model_validate(lane).model_copy(update={
        "access_point_name": ap.name if ap else None,
        "campus_name": ap.campus.name if ap and ap.campus else None,
        "camera_count": len(lane.cameras),
        "controller_count": len(lane.controllers),
    })


def device_out(device, *, is_camera: bool) -> DeviceOut:
    implemented = (
        IMPLEMENTED_CAMERA_DRIVERS if is_camera else IMPLEMENTED_BARRIER_DRIVERS
    )
    lane = device.lane
    return DeviceOut.model_validate(device).model_copy(update={
        "lane_name": lane.name if lane else None,
        "access_point_name": lane.access_point.name if lane and lane.access_point else None,
        "driver_implemented": device.driver in implemented,
    })


# --- People / vehicles ----------------------------------------------------
def person_out(db: Session, p: Person) -> PersonOut:
    count = db.scalar(select(func.count(Vehicle.id)).where(Vehicle.owner_id == p.id)) or 0
    return PersonOut.model_validate(p).model_copy(update={
        "full_name": p.full_name, "vehicle_count": count,
    })


def vehicle_out(db: Session, v: Vehicle) -> VehicleOut:
    now = datetime.now(timezone.utc)
    active = db.scalar(
        select(func.count(Authorization.id)).where(
            Authorization.vehicle_id == v.id,
            Authorization.status == AuthorizationStatus.ACTIVE,
        )
    ) or 0
    watch = db.scalar(
        select(func.count(WatchlistEntry.id)).where(
            WatchlistEntry.plate == v.plate, WatchlistEntry.is_active.is_(True)
        )
    ) or 0
    return VehicleOut.model_validate(v).model_copy(update={
        "owner_name": v.owner.full_name if v.owner else None,
        "active_authorizations": active,
        "is_watchlisted": watch > 0,
    })


def authorization_out(a: Authorization) -> AuthorizationOut:
    now = datetime.now(timezone.utc)
    valid_from = _aware(a.valid_from)
    valid_until = _aware(a.valid_until)
    currently = (
        a.status == AuthorizationStatus.ACTIVE
        and valid_from is not None and valid_from <= now
        and (valid_until is None or valid_until >= now)
    )
    if a.access_point_id and a.access_point_id is not None:
        scope = "Acceso específico"
    elif a.campus_id:
        scope = "Todo el campus"
    else:
        scope = "Institucional"
    return AuthorizationOut.model_validate(a).model_copy(update={
        "plate_display": a.vehicle.plate_display if a.vehicle else None,
        "person_name": a.person.full_name if a.person else None,
        "scope_label": scope,
        "is_currently_valid": currently,
    })


def watchlist_out(w: WatchlistEntry) -> WatchlistOut:
    return WatchlistOut.model_validate(w).model_copy(update={
        "plate_display": format_for_display(w.plate),
        "severity": w.severity.value if hasattr(w.severity, "value") else str(w.severity),
    })


# --- Operations -----------------------------------------------------------
def event_out_db(db: Session, e: AccessEvent) -> AccessEventOut:
    """Session-aware variant that resolves the related names."""
    base = AccessEventOut.model_validate(e)
    lane = db.get(Lane, e.lane_id) if e.lane_id else None
    ap = lane.access_point if lane else None
    person = db.get(Person, e.person_id) if e.person_id else None
    vehicle = db.get(Vehicle, e.vehicle_id) if e.vehicle_id else None
    decider = db.get(User, e.decided_by_id) if e.decided_by_id else None
    return base.model_copy(update={
        "lane_name": lane.name if lane else None,
        "access_point_name": ap.name if ap else None,
        "campus_name": ap.campus.name if ap and ap.campus else None,
        "plate_display": format_for_display(e.plate_normalized) if e.plate_normalized else None,
        "decision_label": DECISION_LABELS.get(e.decision, e.decision.value),
        "reason_label": REASON_LABELS.get(e.decision_reason, e.decision_reason.value),
        "has_evidence": bool(e.evidence_path),
        "person_name": person.full_name if person else None,
        "person_type": person.person_type.value if person else None,
        "vehicle_label": (
            " ".join(x for x in [vehicle.brand, vehicle.model, vehicle.color] if x) or None
        ) if vehicle else None,
        "decided_by_name": decider.full_name if decider else None,
    })


def event_detail(db: Session, e: AccessEvent) -> AccessEventDetail:
    base = event_out_db(db, e)
    detections = [DetectionOut.model_validate(d) for d in e.detections]
    candidates: list = []
    for d in e.detections:
        for c in (d.ocr_candidates or []):
            if c not in candidates:
                candidates.append(c)
    return AccessEventDetail(**base.model_dump(), detections=detections,
                             plate_candidates=candidates)


def incident_out(db: Session, i: Incident) -> IncidentOut:
    vehicle = db.get(Vehicle, i.vehicle_id) if i.vehicle_id else None
    person = db.get(Person, i.person_id) if i.person_id else None
    lane = db.get(Lane, i.lane_id) if i.lane_id else None
    reporter = db.get(User, i.reported_by_id) if i.reported_by_id else None
    resolver = db.get(User, i.resolved_by_id) if i.resolved_by_id else None
    return IncidentOut.model_validate(i).model_copy(update={
        "plate_display": vehicle.plate_display if vehicle else None,
        "person_name": person.full_name if person else None,
        "lane_name": lane.name if lane else None,
        "reported_by_name": reporter.full_name if reporter else None,
        "resolved_by_name": resolver.full_name if resolver else None,
    })


def shift_out(db: Session, s: GuardShift) -> ShiftOut:
    user = db.get(User, s.user_id)
    lane = db.get(Lane, s.lane_id) if s.lane_id else None
    started = _aware(s.started_at)
    ended = _aware(s.ended_at)
    duration = int(((ended or datetime.now(timezone.utc)) - started).total_seconds() // 60) if started else None
    return ShiftOut.model_validate(s).model_copy(update={
        "user_name": user.full_name if user else None,
        "lane_name": lane.name if lane else None,
        "is_open": s.ended_at is None,
        "duration_minutes": duration,
    })


def audit_out(a) -> AuditOut:
    return AuditOut.model_validate(a)
