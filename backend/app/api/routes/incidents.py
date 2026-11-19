"""Incidents: infractions, watchlist hits, unauthorized attempts."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, select

from app.api.deps import AdminOrSupervisor, AnyRole, DbSession, Operators
from app.api.serializers import incident_out
from app.models import AccessEvent, Incident, IncidentStatus, Person, Vehicle
from app.schemas.common import Message, Page
from app.schemas.operations import IncidentIn, IncidentOut, IncidentUpdate
from app.services import audit
from app.services.notifications import notify_incident

router = APIRouter(prefix="/incidents", tags=["incidents"])


@router.get("", response_model=Page[IncidentOut])
def list_incidents(
    db: DbSession, user: AnyRole,
    status_filter: str | None = Query(None, alias="status"),
    severity: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Incident)
    if status_filter:
        stmt = stmt.where(Incident.status == status_filter)
    if severity:
        stmt = stmt.where(Incident.severity == severity)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Incident.created_at.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[IncidentOut](items=[incident_out(db, i) for i in rows], total=total,
                             page=page, page_size=page_size)


@router.post("", response_model=IncidentOut, status_code=status.HTTP_201_CREATED)
def create_incident(payload: IncidentIn, request: Request, db: DbSession, user: Operators):
    if payload.event_id and not db.get(AccessEvent, payload.event_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El evento no existe.")

    incident = Incident(**payload.model_dump(), reported_by_id=user.id)
    # Fill related entities from the event when the guard raised it there.
    if incident.event_id and not incident.vehicle_id:
        event = db.get(AccessEvent, incident.event_id)
        incident.vehicle_id = event.vehicle_id
        incident.person_id = incident.person_id or event.person_id
        incident.lane_id = incident.lane_id or event.lane_id
    db.add(incident)
    db.flush()

    # Attempt the notification and record the actual outcome, rather than
    # assuming it was delivered the way the prototype did.
    person = db.get(Person, incident.person_id) if incident.person_id else None
    sent, error = notify_incident(incident, person)
    if sent:
        incident.notified_at = datetime.now(timezone.utc)
    else:
        incident.notification_error = error

    audit.record(db, action="incidents.created", actor=user, entity_type="incident",
                 entity_id=incident.id, summary=incident.title, request=request)
    db.commit()
    return incident_out(db, incident)


@router.get("/{incident_id}", response_model=IncidentOut)
def get_incident(incident_id: int, db: DbSession, user: AnyRole):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incidencia no encontrada.")
    return incident_out(db, incident)


@router.patch("/{incident_id}", response_model=IncidentOut)
def update_incident(incident_id: int, payload: IncidentUpdate, request: Request,
                    db: DbSession, user: AdminOrSupervisor):
    incident = db.get(Incident, incident_id)
    if not incident:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Incidencia no encontrada.")

    previous = incident.status
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(incident, k, v)

    if payload.status in (IncidentStatus.RESOLVED, IncidentStatus.DISMISSED):
        incident.resolved_by_id = user.id
        incident.resolved_at = datetime.now(timezone.utc)

    audit.record(db, action="incidents.updated", actor=user, entity_type="incident",
                 entity_id=incident.id,
                 summary=f"{previous.value} -> {incident.status.value}", request=request)
    db.commit()
    return incident_out(db, incident)
