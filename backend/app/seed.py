"""Demo dataset.

EVERY row created here is marked `origin = DEMO` and the UI surfaces that
badge. None of this represents real Universidad de Colima records: the
campus names are public, but the gates, lanes, people, plates, permits and
events are invented for development.

Run:  python -m app.seed          (idempotent: skips if data exists)
      python -m app.seed --reset  (drops and recreates — asks first)
"""
from __future__ import annotations

import random
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.core.db import SessionLocal, create_all, engine
from app.core.security import hash_password
from app.models import (
    AccessEvent, AccessPoint, ActuationStatus, Authorization, AuthorizationStatus,
    BarrierController, Base, Camera, Campus, DataOrigin, Decision, DecisionReason,
    Detection, DeviceStatus, EventSource, GuardShift, Incident, IncidentSeverity,
    IncidentStatus, IncidentType, Lane, LaneDirection, Person, PersonType, Role,
    User, Vehicle, WatchlistEntry,
)
from app.recognition.plate import format_for_display

DEMO = DataOrigin.DEMO
NOW = datetime.now(timezone.utc)

# Demo credentials. `must_change_password` is set so nobody ships these.
DEMO_USERS = [
    ("admin",      "Dirección de Seguridad",   Role.ADMIN,      "SCAEadmin2026!"),
    ("supervisor", "Supervisión de Accesos",   Role.SUPERVISOR, "SCAEsuper2026!"),
    ("guardia1",   "Guardia · Puerta Norte",   Role.GUARD,      "SCAEguard2026!"),
    ("guardia2",   "Guardia · Puerta Sur",     Role.GUARD,      "SCAEguard2026!"),
    ("auditor",    "Auditoría Interna",        Role.AUDITOR,    "SCAEaudit2026!"),
]

DEMO_PEOPLE = [
    ("20194501", "Ana Sofía",  "Ramírez Delgado", PersonType.STUDENT,  "Facultad de Ingeniería Mecánica y Eléctrica"),
    ("20201188", "Diego",      "Herrera Cortés",  PersonType.STUDENT,  "Facultad de Telemática"),
    ("20213377", "Mariana",    "Ochoa Barajas",   PersonType.STUDENT,  "Facultad de Contabilidad y Administración"),
    ("A0043120", "Dr. Ernesto","Villaseñor Ruiz", PersonType.ACADEMIC, "Facultad de Telemática"),
    ("A0051002", "Dra. Lucía", "Fuentes Moreno",  PersonType.ACADEMIC, "Facultad de Medicina"),
    ("P0012045", "Roberto",    "Salazar Núñez",   PersonType.STAFF,    "Dirección General de Servicios Generales"),
    ("V0000317", "Carmen",     "Ibarra Lozano",   PersonType.VISITOR,  None),
    ("S0000904", "Miguel",     "Tapia Guzmán",    PersonType.SUPPLIER, None),
]

DEMO_VEHICLES = [
    ("GPT4567", "Nissan",     "Versa",    "Blanco",  2019, 0),
    ("HXR8821", "Volkswagen", "Jetta",    "Gris",    2021, 1),
    ("JBM3094", "Chevrolet",  "Aveo",     "Rojo",    2018, 2),
    ("KDN5512", "Toyota",     "Corolla",  "Negro",   2022, 3),
    ("LFP7730", "Honda",      "CR-V",     "Plata",   2020, 4),
    ("MQS2246", "Ford",       "Ranger",   "Blanco",  2017, 5),
    ("NTV9083", "Kia",        "Rio",      "Azul",    2023, 6),
    ("PWZ1164", "Nissan",     "NP300",    "Blanco",  2016, 7),
    ("RCX4408", "Mazda",      "3",        "Gris",    2021, None),  # sin dueño
]


def _confirm_reset() -> bool:
    answer = input("Esto BORRARÁ todas las tablas de SCAE v2. Escribe 'BORRAR' para continuar: ")
    return answer.strip() == "BORRAR"


def seed(reset: bool = False) -> None:
    if reset:
        if not _confirm_reset():
            print("Cancelado. No se modificó nada.")
            return
        Base.metadata.drop_all(bind=engine)

    create_all()
    db = SessionLocal()
    try:
        if db.scalar(select(func.count(User.id))):
            print("La base ya contiene datos. Nada que sembrar.")
            print("Usa --reset para reconstruir desde cero.")
            return

        # --- Topology ----------------------------------------------------
        campuses = {}
        for code, name, city in [
            ("CUC", "Campus Central Colima",     "Colima"),
            ("CVA", "Campus Villa de Álvarez",   "Villa de Álvarez"),
            ("CCQ", "Campus Coquimatlán",        "Coquimatlán"),
        ]:
            c = Campus(code=code, name=name, city=city, origin=DEMO)
            db.add(c)
            campuses[code] = c
        db.flush()

        points, lanes = {}, {}
        topology = [
            ("CUC", "PN", "Puerta Norte",   [("N1", "Carril Norte 1", LaneDirection.ENTRY),
                                             ("N2", "Carril Norte 2", LaneDirection.EXIT)]),
            ("CUC", "PS", "Puerta Sur",     [("S1", "Carril Sur 1", LaneDirection.BIDIRECTIONAL)]),
            ("CVA", "PP", "Puerta Principal", [("P1", "Carril Principal", LaneDirection.ENTRY)]),
            ("CCQ", "PA", "Acceso Agropecuarias", [("A1", "Carril Agro", LaneDirection.BIDIRECTIONAL)]),
        ]
        for campus_code, ap_code, ap_name, lane_defs in topology:
            ap = AccessPoint(campus_id=campuses[campus_code].id, code=ap_code,
                             name=ap_name, origin=DEMO)
            db.add(ap)
            db.flush()
            points[ap_code] = ap
            for lcode, lname, direction in lane_defs:
                lane = Lane(access_point_id=ap.id, code=lcode, name=lname,
                            direction=direction, origin=DEMO)
                db.add(lane)
                db.flush()
                lanes[lcode] = lane
                db.add(Camera(
                    lane_id=lane.id, code=f"CAM-{lcode}", name=f"Cámara {lname}",
                    driver="mock", config={}, status=DeviceStatus.ONLINE, origin=DEMO,
                ))
                db.add(BarrierController(
                    lane_id=lane.id, code=f"BAR-{lcode}", name=f"Barrera {lname}",
                    driver="mock", config={}, status=DeviceStatus.ONLINE, origin=DEMO,
                ))
        db.flush()

        # --- People / users ----------------------------------------------
        people = []
        for inst_id, first, last, ptype, faculty in DEMO_PEOPLE:
            p = Person(
                institutional_id=inst_id, first_name=first, last_name=last,
                person_type=ptype, faculty=faculty,
                email=f"{first.split()[0].lower()}.{last.split()[0].lower()}@demo.scae.local",
                origin=DEMO,
            )
            db.add(p)
            people.append(p)
        db.flush()

        lane_for_user = {"guardia1": lanes["N1"].id, "guardia2": lanes["S1"].id}
        users = {}
        for username, full_name, role, password in DEMO_USERS:
            u = User(
                username=username, full_name=full_name, role=role,
                email=f"{username}@demo.scae.local",
                password_hash=hash_password(password),
                default_lane_id=lane_for_user.get(username),
                must_change_password=True, origin=DEMO,
            )
            db.add(u)
            users[username] = u
        db.flush()

        # --- Vehicles / authorizations ------------------------------------
        vehicles = []
        for plate, brand, model, color, year, owner_idx in DEMO_VEHICLES:
            v = Vehicle(
                plate=plate, plate_display=format_for_display(plate), brand=brand,
                model=model, color=color, year=year, state_code="COL",
                owner_id=people[owner_idx].id if owner_idx is not None else None,
                origin=DEMO,
            )
            db.add(v)
            vehicles.append(v)
        db.flush()

        # A spread of permit states so every UI branch has something to show.
        auth_specs = [
            (0, AuthorizationStatus.ACTIVE,    None,                  None, "Permiso semestral estudiante"),
            (1, AuthorizationStatus.ACTIVE,    None,                  None, "Permiso semestral estudiante"),
            (2, AuthorizationStatus.SUSPENDED, None,                  None, "Suspendido por infracción reiterada"),
            (3, AuthorizationStatus.ACTIVE,    None,                  None, "Permiso académico anual"),
            (4, AuthorizationStatus.ACTIVE,    None,                  None, "Permiso académico anual"),
            (5, AuthorizationStatus.ACTIVE,    None,                  None, "Personal administrativo"),
            (6, AuthorizationStatus.ACTIVE,    points["PN"].id,       2,    "Pase de visitante — Puerta Norte"),
            (7, AuthorizationStatus.EXPIRED,   None,                  None, "Proveedor — periodo concluido"),
        ]
        for v_idx, status_, ap_id, days, reason in auth_specs:
            vehicle = vehicles[v_idx]
            valid_from = NOW - timedelta(days=120 if days is None else 0)
            if status_ is AuthorizationStatus.EXPIRED:
                valid_from, valid_until = NOW - timedelta(days=200), NOW - timedelta(days=30)
            elif days is not None:
                valid_until = NOW + timedelta(days=days)
            else:
                valid_until = NOW + timedelta(days=180)
            db.add(Authorization(
                vehicle_id=vehicle.id, person_id=vehicle.owner_id,
                campus_id=None if ap_id else campuses["CUC"].id,
                access_point_id=ap_id, status=status_,
                valid_from=valid_from, valid_until=valid_until,
                permit_code=f"UC-{2026}-{1000 + v_idx}", reason=reason, origin=DEMO,
            ))
        db.flush()

        # --- Watchlist -----------------------------------------------------
        db.add(WatchlistEntry(
            plate="TZK9911", severity=IncidentSeverity.HIGH,
            reason="Vehículo reportado en incidente de seguridad (dato demo).",
            created_by_id=users["admin"].id, origin=DEMO,
        ))

        # --- Historical events --------------------------------------------
        rng = random.Random(20260819)  # deterministic demo history
        lane_pool = [lanes["N1"], lanes["N2"], lanes["S1"], lanes["P1"]]
        allowed_vehicles = [vehicles[i] for i in (0, 1, 3, 4, 5)]

        events_created = 0
        for hours_ago in range(72, 0, -1):
            # Busier during working hours, quiet overnight — so the trend
            # chart shows a realistic shape rather than a flat line.
            local_hour = (NOW - timedelta(hours=hours_ago)).hour
            volume = 4 if 7 <= local_hour <= 20 else 1
            for _ in range(rng.randint(0, volume)):
                at = NOW - timedelta(hours=hours_ago, minutes=rng.randint(0, 59))
                lane = rng.choice(lane_pool)
                roll = rng.random()

                if roll < 0.72:
                    vehicle = rng.choice(allowed_vehicles)
                    decision, reason = Decision.ALLOW, DecisionReason.AUTHORIZED
                    plate, conf = vehicle.plate, round(rng.uniform(0.82, 0.98), 3)
                    vehicle_id, person_id = vehicle.id, vehicle.owner_id
                    actuation = ActuationStatus.SIMULATED
                elif roll < 0.84:
                    vehicle = vehicles[2]  # suspended permit
                    decision, reason = Decision.DENY, DecisionReason.AUTHORIZATION_SUSPENDED
                    plate, conf = vehicle.plate, round(rng.uniform(0.80, 0.95), 3)
                    vehicle_id, person_id = vehicle.id, vehicle.owner_id
                    actuation = ActuationStatus.NOT_REQUIRED
                elif roll < 0.93:
                    plate = rng.choice(["QWE1122", "ZXC3344", "BNM5566"])
                    decision, reason = Decision.MANUAL_REVIEW, DecisionReason.UNKNOWN_VEHICLE
                    conf = round(rng.uniform(0.70, 0.94), 3)
                    vehicle_id = person_id = None
                    actuation = ActuationStatus.NOT_REQUIRED
                else:
                    plate = None
                    decision, reason = Decision.MANUAL_REVIEW, DecisionReason.LOW_CONFIDENCE
                    conf = round(rng.uniform(0.20, 0.54), 3)
                    vehicle_id = person_id = None
                    actuation = ActuationStatus.NOT_REQUIRED

                event = AccessEvent(
                    occurred_at=at, lane_id=lane.id, source=EventSource.CAMERA,
                    plate_read=plate, plate_normalized=plate, plate_confidence=conf,
                    vehicle_id=vehicle_id, person_id=person_id,
                    decision=decision, decision_reason=reason,
                    resolved_at=at if decision is not Decision.MANUAL_REVIEW else None,
                    actuation_status=actuation,
                    actuation_detail="Apertura simulada" if actuation is ActuationStatus.SIMULATED else None,
                    processing_ms=rng.randint(380, 1450), origin=DEMO,
                )
                db.add(event)
                events_created += 1
        db.flush()

        # --- Incidents ------------------------------------------------------
        db.add(Incident(
            incident_type=IncidentType.UNAUTHORIZED_ATTEMPT,
            severity=IncidentSeverity.MEDIUM, status=IncidentStatus.OPEN,
            title="Intento de acceso con permiso suspendido",
            description="El vehículo JBM-30-94 intentó ingresar con permiso suspendido.",
            vehicle_id=vehicles[2].id, person_id=vehicles[2].owner_id,
            lane_id=lanes["N1"].id, reported_by_id=users["guardia1"].id,
            notification_error="SMTP no está configurado en este entorno.",
            origin=DEMO,
        ))
        db.add(Incident(
            incident_type=IncidentType.INFRACTION, severity=IncidentSeverity.LOW,
            status=IncidentStatus.RESOLVED,
            title="Estacionamiento en zona restringida",
            description="Reporte del turno matutino (dato demo).",
            vehicle_id=vehicles[0].id, person_id=vehicles[0].owner_id,
            lane_id=lanes["N1"].id, reported_by_id=users["guardia1"].id,
            resolved_by_id=users["supervisor"].id, resolved_at=NOW - timedelta(days=1),
            resolution_notes="Se notificó al propietario. Sin reincidencia.",
            origin=DEMO,
        ))

        # --- Shifts ---------------------------------------------------------
        db.add(GuardShift(user_id=users["guardia1"].id, lane_id=lanes["N1"].id,
                          started_at=NOW - timedelta(hours=30),
                          ended_at=NOW - timedelta(hours=22), origin=DEMO))
        db.add(GuardShift(user_id=users["guardia2"].id, lane_id=lanes["S1"].id,
                          started_at=NOW - timedelta(hours=6), origin=DEMO))

        db.commit()

        print("Datos demo creados (todos marcados origin=DEMO):")
        print(f"  {len(campuses)} campus · {len(points)} accesos · {len(lanes)} carriles")
        print(f"  {len(lanes)} cámaras · {len(lanes)} controladores (driver mock)")
        print(f"  {len(people)} personas · {len(vehicles)} vehículos · {len(auth_specs)} permisos")
        print(f"  {events_created} eventos de acceso · 2 incidencias · 1 lista de control")
        print("\nCuentas demo (deben cambiar contraseña al primer acceso):")
        for username, full_name, role, password in DEMO_USERS:
            print(f"  {username:<12} {password:<18} {role.value}")
    finally:
        db.close()


if __name__ == "__main__":
    seed(reset="--reset" in sys.argv)
