"""Alta de personas y vehiculos desde la caseta.

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

Para que
--------
Cuando la camara lee una placa que no esta en el padron, el motor de decision
la manda a revision manual (regla 4: un vehiculo desconocido **no** es una
denegacion). Hasta ahora el guardia solo podia permitir o denegar el paso, y en
ambos casos la placa seguia sin existir al dia siguiente. Esto le da las dos
salidas que de verdad ocurren en una caseta:

* **Visitante** — entra hoy, por un motivo y un rato. Se le abre un permiso con
  ventana corta y alcance de UNA puerta.
* **Credencial** — es de la universidad y hay que darlo de alta de forma
  permanente. Se piden los datos que vienen impresos en la credencial: nombre,
  numero de cuenta y si es estudiante o trabajador. El permiso queda sin fecha
  de caducidad y con alcance de campus.

No hay tabla de visitantes: un pase de visitante es una `Authorization` normal
con `valid_until` cercano, que es justo lo que el modelo de datos ya preveia.
Eso significa que las nueve reglas del motor de decision se aplican igual a un
visitante que a un rector, sin un segundo camino que mantener.

Despues del alta se vuelve a resolver el MISMO evento con
`access.apply_manual_decision`, asi que el acceso queda firmado por el guardia
que lo autorizo y la barrera se acciona por el camino de siempre. No se crea un
evento nuevo: el que ya existe es el que la camara genero.
"""
from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import (
    AccessEvent, Authorization, AuthorizationStatus, DataOrigin, Decision,
    Lane, Person, PersonType, User, Vehicle,
)
from app.recognition.plate import format_for_display, interpret
from app.services import access

logger = logging.getLogger(__name__)
settings = get_settings()

# Lo que la credencial distingue: alumno o personal. El modelo tiene mas tipos
# (academico, proveedor), pero la caseta solo necesita estos dos y ofrecer
# cinco opciones a las tres de la manana no ayuda a nadie.
CREDENTIAL_TYPES: dict[str, PersonType] = {
    "student": PersonType.STUDENT,
    "staff": PersonType.STAFF,
}


class IntakeError(ValueError):
    """Datos de alta invalidos. La ruta los traduce a un 400."""


def split_name(full_name: str) -> tuple[str, str]:
    """Parte un nombre completo en nombre(s) y apellidos.

    Convencion mexicana: nombre(s) + apellido paterno + apellido materno. Con
    tres o mas palabras, las **dos ultimas** son los apellidos; con dos, una y
    una; con una sola, se queda como nombre y el apellido vacio.

    Es una heuristica, y por eso el nombre tal y como lo tecleo el guardia se
    guarda ademas en `Person.notes`: si la division se equivoca, el dato
    original no se pierde.
    """
    parts = [p for p in (full_name or "").split() if p]
    if not parts:
        raise IntakeError("El nombre completo es obligatorio.")
    if len(parts) == 1:
        return parts[0], ""
    if len(parts) == 2:
        return parts[0], parts[1]
    return " ".join(parts[:-2]), " ".join(parts[-2:])


def _permit_code(prefix: str) -> str:
    """Codigo legible y unico sin consultar la base."""
    now = datetime.now(timezone.utc)
    return f"{prefix}-{now:%y%m%d}-{secrets.token_hex(2).upper()}"


def _resolve_plate(event: AccessEvent, plate_override: Optional[str]) -> str:
    """La placa que se va a registrar.

    El guardia puede corregir la lectura del OCR: si teclea una placa, esa
    manda sobre lo que leyo la camara. Se normaliza siempre, para que la clave
    coincida con la que usara el motor de decision despues.
    """
    raw = (plate_override or "").strip() or event.plate_normalized or event.plate_read
    if not raw:
        raise IntakeError(
            "Este evento no tiene ninguna placa. Teclea la placa para registrarla."
        )
    reading = interpret(raw, ocr_confidence=1.0)
    if not reading.is_valid:
        raise IntakeError(f"La placa '{raw}' no tiene un formato valido.")
    return reading.normalized


def _upsert_vehicle(
    db: Session, plate: str, owner: Person,
    brand: Optional[str], model: Optional[str], color: Optional[str],
) -> Vehicle:
    vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == plate))
    if vehicle is None:
        vehicle = Vehicle(
            plate=plate, plate_display=format_for_display(plate),
            owner_id=owner.id, brand=brand or None, model=model or None,
            color=color or None, is_active=True, origin=DataOrigin.REAL,
        )
        db.add(vehicle)
        db.flush()
        return vehicle

    # Ya existia (placa dada de baja, o alta a medias). Se reactiva y se
    # reasigna el dueno en vez de duplicar la fila: `plate` es unica.
    vehicle.is_active = True
    vehicle.owner_id = owner.id
    vehicle.origin = DataOrigin.REAL
    for field, value in (("brand", brand), ("model", model), ("color", color)):
        if value:
            setattr(vehicle, field, value)
    db.flush()
    return vehicle


def _grant(
    db: Session, *, vehicle: Vehicle, person: Person, lane: Optional[Lane],
    prefix: str, reason: str, hours: Optional[float],
) -> Authorization:
    """Crea el permiso. `hours=None` es permanente.

    Alcance:
      * visitante  -> la puerta concreta por la que entra (lo mas estrecho).
      * permanente -> todo el campus, que es como funciona un tarjeton.
    """
    now = datetime.now(timezone.utc)
    access_point_id = lane.access_point_id if lane else None
    campus_id = lane.access_point.campus_id if lane and lane.access_point else None

    auth = Authorization(
        vehicle_id=vehicle.id,
        person_id=person.id,
        campus_id=campus_id,
        # Un pase de visitante vale para la puerta por la que se le dio;
        # una credencial, para cualquier puerta del campus.
        access_point_id=access_point_id if hours is not None else None,
        status=AuthorizationStatus.ACTIVE,
        valid_from=now,
        valid_until=(now + timedelta(hours=hours)) if hours is not None else None,
        permit_code=_permit_code(prefix),
        reason=reason,
        origin=DataOrigin.REAL,
    )
    db.add(auth)
    db.flush()
    return auth


def _authorize_event(
    db: Session, event: AccessEvent, actor: User, lane: Optional[Lane], note: str,
    plate: str, *, vehicle: Vehicle, person: Person, auth: Authorization,
) -> None:
    """Resuelve el evento como permitido, lo enlaza y acciona la barrera.

    Se delega en `access.apply_manual_decision` a proposito: es el mismo camino
    que usa el boton 'Permitir acceso', asi que la actuacion, la firma del
    guardia y el sello de resolucion salen identicos, sin duplicar logica.

    Lo que `apply_manual_decision` NO puede hacer es enlazar el evento con algo
    que acaba de nacer. Solo reasigna `vehicle_id` cuando se le pasa una placa
    corregida, asi que al registrar una placa que el OCR habia leido bien el
    evento se quedaba con `vehicle_id`, `person_id` y `authorization_id` en
    NULL: el acceso quedaba permitido pero sin dueno. Cualquier informe por
    persona o por vehiculo se saltaba justo las entradas que el guardia acababa
    de dar de alta. Por eso se enlazan aqui de forma explicita.
    """
    access.apply_manual_decision(
        db, event, decision=Decision.ALLOW, actor=actor, notes=note,
        corrected_plate=plate if plate != event.plate_normalized else None,
        open_barrier=True, lane=lane,
    )
    event.plate_normalized = vehicle.plate
    event.vehicle_id = vehicle.id
    event.person_id = person.id
    event.authorization_id = auth.id
    db.flush()


# --------------------------------------------------------------------------
# Las dos altas
# --------------------------------------------------------------------------
def register_visitor(
    db: Session, *, event: AccessEvent, actor: User, lane: Optional[Lane],
    full_name: str, visit_reason: str, hours: Optional[float] = None,
    plate_override: Optional[str] = None,
    brand: Optional[str] = None, model: Optional[str] = None,
    color: Optional[str] = None,
) -> dict:
    """Pase temporal: entra hoy, por un motivo, durante un rato."""
    plate = _resolve_plate(event, plate_override)
    first, last = split_name(full_name)
    reason = (visit_reason or "").strip()
    if not reason:
        raise IntakeError("El motivo de entrada es obligatorio.")

    hours = float(hours if hours is not None else settings.visitor_pass_default_hours)
    if hours <= 0:
        raise IntakeError("La duracion de la visita debe ser mayor que cero.")
    if hours > settings.visitor_pass_max_hours:
        raise IntakeError(
            f"Un pase de visitante no puede durar mas de "
            f"{settings.visitor_pass_max_hours:.0f} horas. Para una estancia mas "
            "larga, registra la placa de forma permanente."
        )

    # Un visitante NO se deduplica por nombre: dos personas pueden llamarse
    # igual, y fusionarlas por el nombre mezclaria los registros de acceso de
    # dos desconocidos. Cada visita crea su propia ficha.
    person = Person(
        first_name=first, last_name=last, person_type=PersonType.VISITOR,
        is_active=True, origin=DataOrigin.REAL,
        notes=f"Visitante registrado en caseta. Nombre tal cual se capturo: "
              f"{full_name.strip()}. Motivo: {reason}",
    )
    db.add(person)
    db.flush()

    vehicle = _upsert_vehicle(db, plate, person, brand, model, color)
    auth = _grant(
        db, vehicle=vehicle, person=person, lane=lane, prefix="VIS",
        reason=reason, hours=hours,
    )
    _authorize_event(
        db, event, actor, lane,
        f"Visitante: {person.full_name}. Motivo: {reason}. "
        f"Pase {auth.permit_code} por {hours:g} h.",
        plate, vehicle=vehicle, person=person, auth=auth,
    )
    logger.info(
        "Visitante dado de alta: %s / %s, pase %s por %s h",
        person.full_name, plate, auth.permit_code, hours,
    )
    return {
        "kind": "visitor", "person_id": person.id, "vehicle_id": vehicle.id,
        "authorization_id": auth.id, "permit_code": auth.permit_code,
        "plate": plate, "plate_display": vehicle.plate_display,
        "person_name": person.full_name,
        "valid_until": auth.valid_until.isoformat() if auth.valid_until else None,
    }


def register_credential(
    db: Session, *, event: AccessEvent, actor: User, lane: Optional[Lane],
    full_name: str, institutional_id: str, person_type: str,
    plate_override: Optional[str] = None,
    brand: Optional[str] = None, model: Optional[str] = None,
    color: Optional[str] = None,
) -> dict:
    """Alta permanente con los datos de la credencial universitaria."""
    plate = _resolve_plate(event, plate_override)
    first, last = split_name(full_name)

    account = (institutional_id or "").strip().upper()
    if not account:
        raise IntakeError("El numero de cuenta es obligatorio.")
    kind = CREDENTIAL_TYPES.get((person_type or "").strip().lower())
    if kind is None:
        raise IntakeError(
            "Indica si la persona es estudiante o trabajador."
        )

    # Aqui si se deduplica, y por el numero de cuenta, que es el identificador
    # que de verdad distingue a una persona en la universidad. Si el mismo
    # alumno registra un segundo coche, se le anade el vehiculo a su ficha en
    # vez de crear un duplicado.
    person = db.scalar(select(Person).where(Person.institutional_id == account))
    if person is None:
        person = Person(
            institutional_id=account, first_name=first, last_name=last,
            person_type=kind, is_active=True, origin=DataOrigin.REAL,
            notes=f"Alta en caseta desde credencial. Nombre tal cual se "
                  f"capturo: {full_name.strip()}",
        )
        db.add(person)
        db.flush()
    else:
        # Ficha existente: se reactiva y se actualiza el tipo, pero NO se
        # machaca el nombre, que pudo venir de un padron mejor que la caseta.
        person.is_active = True
        person.person_type = kind

    vehicle = _upsert_vehicle(db, plate, person, brand, model, color)
    auth = _grant(
        db, vehicle=vehicle, person=person, lane=lane, prefix="PRM",
        reason=f"Alta permanente en caseta ({kind.value}).", hours=None,
    )
    _authorize_event(
        db, event, actor, lane,
        f"Alta permanente: {person.full_name} ({account}, {kind.value}). "
        f"Permiso {auth.permit_code}.",
        plate, vehicle=vehicle, person=person, auth=auth,
    )
    logger.info(
        "Alta permanente: %s / %s / %s, permiso %s",
        person.full_name, account, plate, auth.permit_code,
    )
    return {
        "kind": "credential", "person_id": person.id, "vehicle_id": vehicle.id,
        "authorization_id": auth.id, "permit_code": auth.permit_code,
        "plate": plate, "plate_display": vehicle.plate_display,
        "person_name": person.full_name, "institutional_id": account,
        "person_type": kind.value, "valid_until": None,
    }
