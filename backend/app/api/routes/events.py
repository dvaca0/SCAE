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
    AccessEventDetail, AccessEventOut, CaptureResponse, CredentialIntakeIn,
    IntakeResponse, ManualDecisionIn, ManualEntryIn, ShiftOut, VisitorIntakeIn,
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
        # Solo cuando NO hubo coincidencia exacta: si el vehiculo esta
        # identificado, sugerir parecidos solo confundiria.
        similar_vehicles=(
            [
                {
                    "id": v.id,
                    "plate": v.plate,
                    "plate_display": v.plate_display,
                    "distance": d,
                    "owner_name": v.owner.full_name if v.owner else None,
                    "owner_type": v.owner.person_type.value if v.owner else None,
                    "brand": v.brand,
                    "color": v.color,
                }
                for v, d in access.similar_vehicles(db, event.plate_normalized)
            ]
            if vehicle is None and event.plate_normalized
            else []
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


@router.get("/{event_id}/context", response_model=CaptureResponse)
def event_context(event_id: int, db: DbSession, user: Operators):
    """El evento con su identidad, vehiculo y permiso resueltos.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    `/events/{id}` y `/events/pending` devuelven el evento, pero no a QUIEN
    corresponde la placa. La consola necesita lo segundo para pintar el panel de
    decision, y antes se lo inventaba: construia un `CaptureResponse` con
    `matched_person`, `matched_vehicle` y `active_authorization` vacios. El
    efecto era que al abrir un pendiente la identidad salia siempre en blanco
    y un vehiculo YA REGISTRADO se ofrecia como desconocido, invitando al
    guardia a darlo de alta por segunda vez.

    Esta ruta reutiliza exactamente el mismo contexto que devuelve una captura,
    asi que un evento abierto desde la cola se ve igual que recien capturado.
    """
    event = db.get(AccessEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento no encontrado.")
    return _capture_context(db, event)


@router.get("/lane/{lane_id}/latest", response_model=CaptureResponse)
def latest_lane_event(lane_id: int, db: DbSession, user: Operators):
    """El ultimo evento del carril, con su contexto.

    PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

    Es lo que la consola consulta para enterarse de que ha pasado un coche sin
    que el guardia tenga que hacer clic en nada. Se sirve con el contexto ya
    resuelto para que una sola peticion baste: a 3 segundos de intervalo, pedir
    primero el evento y luego su identidad doblaria el trafico para nada.
    """
    lane = db.get(Lane, lane_id)
    if lane is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "El carril no existe.")
    if user.role is Role.GUARD and user.default_lane_id and lane.id != user.default_lane_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Solo puedes consultar el carril que tienes asignado.")
    event = db.scalar(
        select(AccessEvent)
        .where(AccessEvent.lane_id == lane_id)
        .order_by(AccessEvent.occurred_at.desc(), AccessEvent.id.desc())
        .limit(1)
    )
    if event is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "Todavia no hay eventos en este carril.")
    return _capture_context(db, event)


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


# --- Alta desde la caseta (PARCHE LOCAL, Raspberry Pi 5) ------------------
# El motor de decision manda los vehiculos desconocidos a revision manual. Estas
# dos rutas son lo que el guardia hace a continuacion: darle paso como visitante
# o registrarlo de forma permanente. Ambas resuelven el MISMO evento y accionan
# la barrera por el camino normal; ver app/services/intake.py.
def _intake_event(db, event_id: int, user) -> tuple[AccessEvent, Lane | None]:
    """Carga el evento y comprueba que este guardia puede tocarlo."""
    event = db.get(AccessEvent, event_id)
    if not event:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Evento no encontrado.")
    if user.role is Role.GUARD and user.default_lane_id and event.lane_id != user.default_lane_id:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Solo puedes resolver eventos de tu carril.")
    if event.resolved_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Este evento ya fue resuelto.")
    lane = db.get(Lane, event.lane_id) if event.lane_id else None
    return event, lane


@router.post("/{event_id}/register-visitor", response_model=IntakeResponse)
def register_visitor_route(
    event_id: int, payload: VisitorIntakeIn, request: Request,
    db: DbSession, user: Operators,
):
    """Pase de visitante: alta temporal y paso autorizado."""
    from app.services import intake

    event, lane = _intake_event(db, event_id, user)
    try:
        result = intake.register_visitor(
            db, event=event, actor=user, lane=lane,
            full_name=payload.full_name, visit_reason=payload.visit_reason,
            hours=payload.hours, plate_override=payload.plate,
            brand=payload.brand, model=payload.model, color=payload.color,
        )
    except intake.IntakeError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(
        db, action="intake.visitor", actor=user, entity_type="access_event",
        entity_id=event.id,
        summary=f"Visitante {result['person_name']} - {result['plate']} "
                f"(pase {result['permit_code']})",
        payload={"permit_code": result["permit_code"],
                 "valid_until": result["valid_until"],
                 "person_id": result["person_id"]},
        request=request,
    )
    db.commit()
    db.refresh(event)
    return IntakeResponse(event=event_detail(db, event), registration=result)


@router.post("/{event_id}/register-credential", response_model=IntakeResponse)
def register_credential_route(
    event_id: int, payload: CredentialIntakeIn, request: Request,
    db: DbSession, user: Operators,
):
    """Alta permanente con los datos de la credencial universitaria."""
    from app.services import intake

    event, lane = _intake_event(db, event_id, user)
    try:
        result = intake.register_credential(
            db, event=event, actor=user, lane=lane,
            full_name=payload.full_name,
            institutional_id=payload.institutional_id,
            person_type=payload.person_type, plate_override=payload.plate,
            brand=payload.brand, model=payload.model, color=payload.color,
        )
    except intake.IntakeError as exc:
        db.rollback()
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    audit.record(
        db, action="intake.credential", actor=user, entity_type="access_event",
        entity_id=event.id,
        summary=f"Alta permanente {result['person_name']} "
                f"({result['institutional_id']}) - {result['plate']}",
        payload={"permit_code": result["permit_code"],
                 "institutional_id": result["institutional_id"],
                 "person_type": result["person_type"],
                 "person_id": result["person_id"]},
        request=request,
    )
    db.commit()
    db.refresh(event)
    return IntakeResponse(event=event_detail(db, event), registration=result)


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
