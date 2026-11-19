"""People, vehicles, authorizations and the security watchlist."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import func, or_, select

from app.api.deps import AdminOnly, AdminOrSupervisor, AnyRole, DbSession, Operators
from app.api.serializers import (
    authorization_out, person_out, vehicle_out, watchlist_out,
)
from app.models import (
    Authorization, AuthorizationStatus, DataOrigin, IncidentSeverity, Person,
    Vehicle, WatchlistEntry,
)
from app.recognition.plate import format_for_display, normalize_for_storage
from app.schemas.catalog import (
    AuthorizationIn, AuthorizationOut, AuthorizationStatusUpdate, PersonIn,
    PersonOut, VehicleIn, VehicleOut, WatchlistIn, WatchlistOut,
)
from app.schemas.common import Message, Page
from app.services import audit

router = APIRouter(tags=["registry"])


# --- People ---------------------------------------------------------------
@router.get("/people", response_model=Page[PersonOut])
def list_people(
    db: DbSession, user: AnyRole,
    q: str | None = None,
    person_type: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Person)
    if q:
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Person.first_name.ilike(like), Person.last_name.ilike(like),
            Person.institutional_id.ilike(like), Person.email.ilike(like),
        ))
    if person_type:
        stmt = stmt.where(Person.person_type == person_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Person.last_name, Person.first_name)
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[PersonOut](items=[person_out(db, p) for p in rows], total=total,
                           page=page, page_size=page_size)


@router.post("/people", response_model=PersonOut, status_code=status.HTTP_201_CREATED)
def create_person(payload: PersonIn, request: Request, db: DbSession,
                  user: AdminOrSupervisor):
    data = payload.model_dump()
    if data.get("institutional_id"):
        exists = db.scalar(
            select(Person).where(Person.institutional_id == data["institutional_id"])
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT,
                                "Ya existe una persona con ese identificador institucional.")
    person = Person(**data)
    db.add(person)
    db.flush()
    audit.record(db, action="registry.person_created", actor=user, entity_type="person",
                 entity_id=person.id, summary=person.full_name, request=request)
    db.commit()
    return person_out(db, person)


@router.get("/people/{person_id}", response_model=PersonOut)
def get_person(person_id: int, db: DbSession, user: AnyRole):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona no encontrada.")
    return person_out(db, person)


@router.put("/people/{person_id}", response_model=PersonOut)
def update_person(person_id: int, payload: PersonIn, request: Request,
                  db: DbSession, user: AdminOrSupervisor):
    person = db.get(Person, person_id)
    if not person:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Persona no encontrada.")
    for k, v in payload.model_dump().items():
        setattr(person, k, v)
    audit.record(db, action="registry.person_updated", actor=user, entity_type="person",
                 entity_id=person.id, summary=person.full_name, request=request)
    db.commit()
    return person_out(db, person)


# --- Vehicles -------------------------------------------------------------
@router.get("/vehicles", response_model=Page[VehicleOut])
def list_vehicles(
    db: DbSession, user: AnyRole,
    q: str | None = None,
    owner_id: int | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Vehicle)
    if q:
        needle = normalize_for_storage(q)
        like = f"%{q.strip()}%"
        stmt = stmt.where(or_(
            Vehicle.plate.contains(needle),
            Vehicle.brand.ilike(like), Vehicle.model.ilike(like),
        ))
    if owner_id:
        stmt = stmt.where(Vehicle.owner_id == owner_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Vehicle.plate).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[VehicleOut](items=[vehicle_out(db, v) for v in rows], total=total,
                            page=page, page_size=page_size)


@router.post("/vehicles", response_model=VehicleOut, status_code=status.HTTP_201_CREATED)
def create_vehicle(payload: VehicleIn, request: Request, db: DbSession,
                   user: AdminOrSupervisor):
    data = payload.model_dump()
    if db.scalar(select(Vehicle).where(Vehicle.plate == data["plate"])):
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa placa ya está registrada.")
    if data.get("owner_id") and not db.get(Person, data["owner_id"]):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "La persona propietaria no existe.")
    vehicle = Vehicle(**data, plate_display=format_for_display(data["plate"]))
    db.add(vehicle)
    db.flush()
    audit.record(db, action="registry.vehicle_created", actor=user, entity_type="vehicle",
                 entity_id=vehicle.id, summary=vehicle.plate_display, request=request)
    db.commit()
    return vehicle_out(db, vehicle)


@router.get("/vehicles/{vehicle_id}", response_model=VehicleOut)
def get_vehicle(vehicle_id: int, db: DbSession, user: AnyRole):
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehículo no encontrado.")
    return vehicle_out(db, vehicle)


@router.put("/vehicles/{vehicle_id}", response_model=VehicleOut)
def update_vehicle(vehicle_id: int, payload: VehicleIn, request: Request,
                   db: DbSession, user: AdminOrSupervisor):
    vehicle = db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Vehículo no encontrado.")
    data = payload.model_dump()
    clash = db.scalar(
        select(Vehicle).where(Vehicle.plate == data["plate"], Vehicle.id != vehicle_id)
    )
    if clash:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa placa pertenece a otro vehículo.")
    for k, v in data.items():
        setattr(vehicle, k, v)
    vehicle.plate_display = format_for_display(vehicle.plate)
    audit.record(db, action="registry.vehicle_updated", actor=user, entity_type="vehicle",
                 entity_id=vehicle.id, summary=vehicle.plate_display, request=request)
    db.commit()
    return vehicle_out(db, vehicle)


# --- Authorizations -------------------------------------------------------
@router.get("/authorizations", response_model=Page[AuthorizationOut])
def list_authorizations(
    db: DbSession, user: AnyRole,
    vehicle_id: int | None = None,
    status_filter: str | None = Query(None, alias="status"),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    stmt = select(Authorization)
    if vehicle_id:
        stmt = stmt.where(Authorization.vehicle_id == vehicle_id)
    if status_filter:
        stmt = stmt.where(Authorization.status == status_filter)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(Authorization.valid_from.desc())
        .offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[AuthorizationOut](items=[authorization_out(a) for a in rows],
                                  total=total, page=page, page_size=page_size)


@router.post("/authorizations", response_model=AuthorizationOut,
             status_code=status.HTTP_201_CREATED)
def create_authorization(payload: AuthorizationIn, request: Request,
                         db: DbSession, user: AdminOrSupervisor):
    vehicle = db.get(Vehicle, payload.vehicle_id)
    if not vehicle:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El vehículo no existe.")
    if payload.valid_until and payload.valid_until <= payload.valid_from:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            "La fecha de fin debe ser posterior a la de inicio.")
    data = payload.model_dump()
    # Default the holder to the vehicle owner so the audit trail names a person.
    if data.get("person_id") is None:
        data["person_id"] = vehicle.owner_id
    auth_row = Authorization(**data)
    db.add(auth_row)
    db.flush()
    audit.record(db, action="registry.authorization_created", actor=user,
                 entity_type="authorization", entity_id=auth_row.id,
                 summary=f"{vehicle.plate_display} · {auth_row.status.value}",
                 request=request)
    db.commit()
    return authorization_out(auth_row)


@router.patch("/authorizations/{auth_id}/status", response_model=AuthorizationOut)
def update_authorization_status(auth_id: int, payload: AuthorizationStatusUpdate,
                                request: Request, db: DbSession, user: AdminOrSupervisor):
    auth_row = db.get(Authorization, auth_id)
    if not auth_row:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Permiso no encontrado.")
    previous = auth_row.status
    auth_row.status = payload.status
    if payload.reason:
        auth_row.reason = payload.reason
    audit.record(db, action="registry.authorization_status_changed", actor=user,
                 entity_type="authorization", entity_id=auth_row.id,
                 summary=f"{previous.value} -> {payload.status.value}",
                 payload={"reason": payload.reason}, request=request)
    db.commit()
    return authorization_out(auth_row)


# --- Watchlist ------------------------------------------------------------
@router.get("/watchlist", response_model=list[WatchlistOut])
def list_watchlist(db: DbSession, user: AnyRole, active_only: bool = True):
    stmt = select(WatchlistEntry).order_by(WatchlistEntry.created_at.desc())
    if active_only:
        stmt = stmt.where(WatchlistEntry.is_active.is_(True))
    return [watchlist_out(w) for w in db.scalars(stmt)]


@router.post("/watchlist", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def add_watchlist(payload: WatchlistIn, request: Request, db: DbSession,
                  user: AdminOrSupervisor):
    existing = db.scalar(select(WatchlistEntry).where(WatchlistEntry.plate == payload.plate))
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, "Esa placa ya está en la lista.")
    entry = WatchlistEntry(
        plate=payload.plate, severity=IncidentSeverity(payload.severity),
        reason=payload.reason, is_active=payload.is_active, created_by_id=user.id,
    )
    db.add(entry)
    db.flush()
    audit.record(db, action="registry.watchlist_added", actor=user,
                 entity_type="watchlist_entry", entity_id=entry.id,
                 summary=f"{entry.plate}: {entry.reason}", request=request)
    db.commit()
    return watchlist_out(entry)


@router.delete("/watchlist/{entry_id}", response_model=Message)
def deactivate_watchlist(entry_id: int, request: Request, db: DbSession,
                         user: AdminOrSupervisor):
    """Deactivates rather than deletes: removing a security flag must remain
    visible in the record."""
    entry = db.get(WatchlistEntry, entry_id)
    if not entry:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Entrada no encontrada.")
    entry.is_active = False
    audit.record(db, action="registry.watchlist_deactivated", actor=user,
                 entity_type="watchlist_entry", entity_id=entry.id,
                 summary=entry.plate, request=request)
    db.commit()
    return Message(detail="Entrada desactivada.")
