"""Access events: the operational core.

`POST /events/capture` is the endpoint the guard console calls with a frame.
It replaces the prototype's `/guard/process_image`, but returns a full event
record instead of a loose status string, so the console can render the whole
decision — plate, identity, permit, reason, evidence — from one response.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import (
    APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile,
    status,
)
from fastapi.responses import FileResponse
from sqlalchemy import func, select

from app.api.deps import AnyRole, DbSession, Operators
from app.api.serializers import event_detail, event_out_db, shift_out
from app.core.config import get_settings
from app.models import (
    AccessEvent, Authorization, AuthorizationStatus, Decision, DecisionReason,
    EventSource, GuardShift, Lane, Person, Role, Vehicle,
)
from app.recognition.pipeline import get_pipeline
from app.schemas.common import Message, Page
from app.schemas.operations import (
    AccessEventDetail, AccessEventOut, CaptureResponse, ManualDecisionIn,
    ManualEntryIn, ShiftOut,
)
from app.services import access, audit

router = APIRouter(prefix="/events", tags=["events"])
settings = get_settings()

ALLOWED_IMAGE_TYPES = {"image/jpeg", "image/png", "image/webp", "image/bmp"}


def _resolve_lane(db, lane_id: int | None, user) -> Lane | None:
    target = lane_id or user.default_lane_id
    if not target:
        return None
    lane = db.get(Lane, target)
    if lane is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El carril indicado no existe.")
    # A guard may only operate their assigned lane.
    if user.role is Role.GUARD and user.default_lane_id and lane.id != user.default_lane_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Sólo puedes operar el carril que tienes asignado.")
    return lane


def _capture_context(db, event: AccessEvent) -> CaptureResponse:
    person = db.get(Person, event.person_id) if event.person_id else None
    vehicle = db.get(Vehicle, event.vehicle_id) if event.vehicle_id else None
    auth_row = db.get(Authorization, event.authorization_id) if event.authorization_id else None
    return CaptureResponse(
        event=event_detail(db, event),
        matched_person=(
            {
                "id": person.id, "full_name": person.full_name,
                "institutional_id": person.institutional_id,
                "person_type": person.person_type.value, "faculty": person.faculty,
                "photo_url": person.photo_url, "email": person.email,
            } if person else None
        ),
        matched_vehicle=(
            {
                "id": vehicle.id, "plate_display": vehicle.plate_display,
                "brand": vehicle.brand, "model": vehicle.model,
                "color": vehicle.color, "year": vehicle.year,
            } if vehicle else None
        ),
        active_authorization=(
            {
                "id": auth_row.id, "status": auth_row.status.value,
                "permit_code": auth_row.permit_code,
                "valid_until": auth_row.valid_until.isoformat() if auth_row.valid_until else None,
            } if auth_row else None
        ),
        recognition_backend=settings.recognition_backend,
    )


@router.post("/capture", response_model=CaptureResponse)
async def capture(
    request: Request,
    db: DbSession,
    user: Operators,
    image: UploadFile = File(...),
    lane_id: int | None = Form(None),
    notes: str | None = Form(None),
):
    """Process a frame captured at a lane."""
    if image.content_type not in ALLOWED_IMAGE_TYPES:
        raise HTTPException(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            f"Formato no admitido ({image.content_type}). Usa JPEG, PNG o WebP.",
        )
    data = await image.read()
    if not data:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La imagen está vacía.")
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            f"La imagen supera el límite de {settings.max_upload_bytes // (1024*1024)} MB.",
        )

    lane = _resolve_lane(db, lane_id, user)
    event = access.process_capture(
        db, image_bytes=data, lane=lane, actor=user,
        source=EventSource.MANUAL_UPLOAD, notes=notes,
    )
    audit.record(db, action="events.capture", actor=user, entity_type="access_event",
                 entity_id=event.id,
                 summary=f"{event.decision.value} · {event.plate_normalized or 'sin placa'}",
                 request=request)
    db.commit()
    db.refresh(event)
    return _capture_context(db, event)


@router.post("/manual", response_model=CaptureResponse)
def manual_entry(payload: ManualEntryIn, request: Request, db: DbSession, user: Operators):
    """Guard types a plate: camera down, damaged plate, motorcycle."""
    lane = _resolve_lane(db, payload.lane_id, user)
    event = access.process_capture(
        db, image_bytes=None, lane=lane, actor=user,
        source=EventSource.MANUAL_ENTRY, typed_plate=payload.plate,
        notes=payload.notes,
    )
    audit.record(db, action="events.manual_entry", actor=user,
                 entity_type="access_event", entity_id=event.id,
                 summary=f"Captura manual {payload.plate}", request=request)
    db.commit()
    db.refresh(event)
    return _capture_context(db, event)


@router.get("", response_model=Page[AccessEventOut])
def list_events(
    db: DbSession, user: AnyRole,
    decision: str | None = None,
    lane_id: int | None = None,
    plate: str | None = None,
    pending_only: bool = False,
    hours: int | None = Query(None, ge=1, le=24 * 90),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(AccessEvent)
    if decision:
        stmt = stmt.where(AccessEvent.decision == decision)
    if lane_id:
        stmt = stmt.where(AccessEvent.lane_id == lane_id)
    if plate:
        from app.recognition.plate import canonicalize
        stmt = stmt.where(AccessEvent.plate_normalized.contains(canonicalize(plate)))
    if pending_only:
        stmt = stmt.where(
            AccessEvent.decision == Decision.MANUAL_REVIEW,
            AccessEvent.resolved_at.is_(None),
        )
    if hours:
        since = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = stmt.where(AccessEvent.occurred_at >= since)

    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AccessEvent.occurred_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[AccessEventOut](items=[event_out_db(db, e) for e in rows],
                                total=total, page=page, page_size=page_size)


@router.get("/pending", response_model=list[AccessEventDetail])
def pending_review(db: DbSession, user: Operators, lane_id: int | None = None,
                   limit: int = Query(20, ge=1, le=100)):
    """The guard console's work queue."""
    stmt = select(AccessEvent).where(
        AccessEvent.decision == Decision.MANUAL_REVIEW,
        AccessEvent.resolved_at.is_(None),
    )
    if lane_id:
        stmt = stmt.where(AccessEvent.lane_id == lane_id)
    rows = db.scalars(stmt.order_by(AccessEvent.occurred_at.desc()).limit(limit)).all()
    return [event_detail(db, e) for e in rows]


@router.get("/{event_id}", response_model=AccessEventDetail)
def get_event(event_id: int, db: DbSession, user: AnyRole):
    event = db.get(AccessEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento no encontrado.")
    return event_detail(db, event)


@router.post("/{event_id}/decision", response_model=AccessEventDetail)
def resolve_event(event_id: int, payload: ManualDecisionIn, request: Request,
                  db: DbSession, user: Operators):
    """A human resolves a review, or overrides an automatic decision."""
    event = db.get(AccessEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento no encontrado.")
    if payload.decision is Decision.MANUAL_REVIEW:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "Debes resolver el evento como permitido o denegado.")

    lane = db.get(Lane, event.lane_id) if event.lane_id else None
    if user.role is Role.GUARD and user.default_lane_id and event.lane_id != user.default_lane_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Sólo puedes resolver eventos de tu carril.")

    previous = event.decision
    access.apply_manual_decision(
        db, event, decision=payload.decision, actor=user, notes=payload.notes,
        corrected_plate=payload.corrected_plate, open_barrier=payload.open_barrier,
        lane=lane,
    )
    audit.record(db, action="events.manual_decision", actor=user,
                 entity_type="access_event", entity_id=event.id,
                 summary=f"{previous.value} -> {payload.decision.value}",
                 payload={"notes": payload.notes,
                          "corrected_plate": payload.corrected_plate},
                 request=request)
    db.commit()
    db.refresh(event)
    return event_detail(db, event)


@router.get("/{event_id}/evidence")
def get_evidence(event_id: int, db: DbSession, user: AnyRole):
    """Serve the stored frame.

    The path comes from the database, never from the request, and is
    re-checked to be inside the evidence directory before being served.
    """
    event = db.get(AccessEvent, event_id)
    if not event or not event.evidence_path:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Sin evidencia para este evento.")
    root = settings.evidence_dir.resolve()
    target = (root / event.evidence_path).resolve()
    if not target.is_file() or root not in target.parents:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evidencia no disponible.")
    return FileResponse(target, media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})


# --- Shifts ---------------------------------------------------------------
@router.get("/shifts/recent", response_model=list[ShiftOut])
def recent_shifts(db: DbSession, user: AnyRole, limit: int = Query(25, ge=1, le=100)):
    rows = db.scalars(
        select(GuardShift).order_by(GuardShift.started_at.desc()).limit(limit)
    ).all()
    return [shift_out(db, s) for s in rows]
