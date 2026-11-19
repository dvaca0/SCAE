"""Alta de visitantes y credenciales desde la caseta.

PARCHE LOCAL (Raspberry Pi 5) - ver /opt/scae/PATCHES.md

Lo que importa comprobar aqui no es que el formulario responda 200, sino que el
alta deje la base en un estado que el motor de decision entienda: si el permiso
que se crea no cubre la puerta por la que entro el coche, la siguiente vez que
pase se le denegaria el acceso al dia siguiente de haberlo registrado. Por eso
varias pruebas vuelven a pasar la MISMA placa despues del alta.
"""
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import (
    AccessEvent, Authorization, AuthorizationStatus, Person, PersonType, Vehicle,
)
from app.services.intake import IntakeError, split_name

from .conftest import admin, guard, supervisor  # noqa: F401

API = "/api/v1"


# --------------------------------------------------------------------------
# Division del nombre
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "full,first,last",
    [
        ("Juan Carlos Perez Lopez", "Juan Carlos", "Perez Lopez"),
        ("Maria Fernanda Lopez Ramirez", "Maria Fernanda", "Lopez Ramirez"),
        ("Ana Perez Lopez", "Ana", "Perez Lopez"),
        ("Ana Perez", "Ana", "Perez"),
        ("Madonna", "Madonna", ""),
        ("  Ana   Perez   Lopez  ", "Ana", "Perez Lopez"),
    ],
)
def test_split_name_follows_the_mexican_convention(full, first, last):
    """Las dos ultimas palabras son los apellidos; el resto, el nombre."""
    assert split_name(full) == (first, last)


def test_split_name_rejects_an_empty_name():
    with pytest.raises(IntakeError):
        split_name("   ")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def _unknown_plate_event(guard, database, plate: str) -> dict:
    """Crea un evento con una placa que no esta en el padron."""
    r = guard.post(f"{API}/events/manual",
                   json={"plate": plate, "lane_id": database["lane_id"]})
    assert r.status_code == 200, r.text
    event = r.json()["event"]
    assert event["decision"] == "manual_review"
    assert event["decision_reason"] == "unknown_vehicle"
    return event


# --------------------------------------------------------------------------
# Visitante
# --------------------------------------------------------------------------
def test_visitor_intake_creates_person_vehicle_and_a_temporary_pass(guard, database):
    event = _unknown_plate_event(guard, database, "VIS-10-01")
    r = guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Maria Fernanda Lopez Ramirez",
              "visit_reason": "Entrega de material", "hours": 3},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    reg, ev = body["registration"], body["event"]

    assert reg["kind"] == "visitor"
    assert reg["person_name"] == "Maria Fernanda Lopez Ramirez"
    assert reg["permit_code"].startswith("VIS-")
    # Un pase de visitante CADUCA. Si esto fuera None seria permanente.
    assert reg["valid_until"] is not None

    # El evento queda resuelto y permitido, sin un segundo clic del guardia.
    assert ev["decision"] == "allow"
    assert ev["resolved_at"] is not None
    assert ev["is_manual"] is True


def test_visitor_is_registered_as_a_visitor_not_a_student(guard, database):
    event = _unknown_plate_event(guard, database, "VIS-10-02")
    r = guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Jorge Ramirez Soto", "visit_reason": "Proveedor"},
    )
    assert r.status_code == 200, r.text
    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        person = db.get(Person, r.json()["registration"]["person_id"])
        assert person.person_type is PersonType.VISITOR
        # El nombre tal cual se tecleo se conserva, por si la division fallo.
        assert "Jorge Ramirez Soto" in (person.notes or "")
    finally:
        db.close()


def test_visitor_pass_covers_the_gate_it_was_issued_for(guard, database):
    """Si el permiso no cubriera la puerta, el coche entraria hoy y se quedaria
    fuera en la siguiente lectura. Es el fallo mas facil de no ver."""
    event = _unknown_plate_event(guard, database, "VIS-10-03")
    assert guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Luis Mendoza Cruz", "visit_reason": "Trámite"},
    ).status_code == 200

    again = guard.post(
        f"{API}/events/manual",
        json={"plate": "VIS-10-03", "lane_id": database["lane_id"]},
    ).json()["event"]
    assert again["decision"] == "allow"
    assert again["decision_reason"] == "authorized"


def test_visitor_pass_longer_than_the_maximum_is_rejected(guard, database):
    event = _unknown_plate_event(guard, database, "VIS-10-04")
    r = guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Ana Perez Lopez", "visit_reason": "Visita larga",
              "hours": 48},
    )
    # 422 lo rechaza el esquema (le <= 24); 400 lo rechazaria el servicio.
    assert r.status_code in (400, 422)


def test_two_visitors_with_the_same_name_are_not_merged(guard, database):
    """Dos personas pueden llamarse igual. Fusionarlas por el nombre mezclaria
    los accesos de dos desconocidos."""
    ids = []
    for plate in ("VIS-10-05", "VIS-10-06"):
        event = _unknown_plate_event(guard, database, plate)
        r = guard.post(
            f"{API}/events/{event['id']}/register-visitor",
            json={"full_name": "Juan Perez Lopez", "visit_reason": "Visita"},
        )
        assert r.status_code == 200, r.text
        ids.append(r.json()["registration"]["person_id"])
    assert ids[0] != ids[1]


# --------------------------------------------------------------------------
# Credencial
# --------------------------------------------------------------------------
def test_credential_intake_creates_a_permanent_permit(guard, database):
    event = _unknown_plate_event(guard, database, "PRM-20-01")
    r = guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Diego Vaca Gonzalez",
              "institutional_id": "90001", "person_type": "student"},
    )
    assert r.status_code == 200, r.text
    reg = r.json()["registration"]
    assert reg["kind"] == "credential"
    assert reg["person_type"] == "student"
    assert reg["permit_code"].startswith("PRM-")
    # Permanente: sin fecha de caducidad.
    assert reg["valid_until"] is None


def test_credential_intake_makes_the_next_entry_automatic(guard, database):
    """El objetivo de registrar la placa: que manana no haga falta el guardia."""
    event = _unknown_plate_event(guard, database, "PRM-20-02")
    assert guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Sofia Ruiz Martinez",
              "institutional_id": "90002", "person_type": "staff"},
    ).status_code == 200

    again = guard.post(
        f"{API}/events/manual",
        json={"plate": "PRM-20-02", "lane_id": database["lane_id"]},
    ).json()["event"]
    assert again["decision"] == "allow"
    assert again["decision_reason"] == "authorized"
    assert again["person_name"] == "Sofia Ruiz Martinez"


def test_second_vehicle_reuses_the_same_person(guard, database):
    """El mismo numero de cuenta no debe producir dos fichas."""
    first = _unknown_plate_event(guard, database, "PRM-20-03")
    a = guard.post(
        f"{API}/events/{first['id']}/register-credential",
        json={"full_name": "Pablo Nunez Vega",
              "institutional_id": "90003", "person_type": "student"},
    )
    assert a.status_code == 200, a.text

    second = _unknown_plate_event(guard, database, "PRM-20-04")
    b = guard.post(
        f"{API}/events/{second['id']}/register-credential",
        json={"full_name": "Pablo Nunez Vega",
              "institutional_id": "90003", "person_type": "student"},
    )
    assert b.status_code == 200, b.text
    assert a.json()["registration"]["person_id"] == b.json()["registration"]["person_id"]
    assert a.json()["registration"]["vehicle_id"] != b.json()["registration"]["vehicle_id"]


def test_credential_requires_a_person_type(guard, database):
    event = _unknown_plate_event(guard, database, "PRM-20-05")
    r = guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Ana Perez Lopez", "institutional_id": "90005",
              "person_type": "rector"},
    )
    assert r.status_code in (400, 422)


# --------------------------------------------------------------------------
# Correccion de placa y reglas de acceso
# --------------------------------------------------------------------------
def test_guard_can_correct_the_plate_while_registering(guard, database):
    """El guardia tiene la placa real delante justo cuando teclea el nombre.
    Registrar la lectura equivocada ensuciaria el padron para siempre."""
    event = _unknown_plate_event(guard, database, "BAD-11-11")
    r = guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Carmen Diaz Flores", "visit_reason": "Visita",
              "plate": "GPT-99-11"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["registration"]["plate"] == "GPT9911"

    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        # La placa mal leida NO debe quedar registrada como un vehiculo.
        assert db.scalar(select(Vehicle).where(Vehicle.plate == "BAD1111")) is None
        assert db.scalar(select(Vehicle).where(Vehicle.plate == "GPT9911")) is not None
    finally:
        db.close()


def test_an_already_resolved_event_cannot_be_registered_twice(guard, database):
    event = _unknown_plate_event(guard, database, "DUP-30-01")
    body = {"full_name": "Rosa Lima Castro", "visit_reason": "Visita"}
    assert guard.post(f"{API}/events/{event['id']}/register-visitor",
                      json=body).status_code == 200
    second = guard.post(f"{API}/events/{event['id']}/register-visitor", json=body)
    assert second.status_code == 409


def test_intake_on_a_missing_event_is_a_404(guard, database):
    r = guard.post(f"{API}/events/999999/register-visitor",
                   json={"full_name": "Nadie Aqui Presente", "visit_reason": "x y z"})
    assert r.status_code == 404


def test_auditor_cannot_register_anyone(client, database):
    """El auditor es de solo lectura. Dar de alta a una persona y abrir la
    barrera no es leer."""
    from .conftest import sign_in

    sign_in(client, "t_audit")
    r = client.post(f"{API}/events/1/register-visitor",
                    json={"full_name": "Ana Perez Lopez", "visit_reason": "Visita"})
    assert r.status_code == 403


def test_registration_is_written_to_the_audit_trail(guard, admin, database):
    event = _unknown_plate_event(guard, database, "AUD-40-01")
    assert guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Hugo Salas Pena", "institutional_id": "90040",
              "person_type": "student"},
    ).status_code == 200

    trail = admin.get(f"{API}/audit?page_size=50").json()["items"]
    actions = [row["action"] for row in trail]
    assert "intake.credential" in actions


def test_the_new_authorization_is_active_and_valid_now(guard, database):
    """Un permiso creado con validez futura dejaria al coche fuera."""
    event = _unknown_plate_event(guard, database, "NOW-50-01")
    r = guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Elena Mora Vidal", "visit_reason": "Visita"},
    )
    assert r.status_code == 200, r.text

    from app.core.db import SessionLocal

    db = SessionLocal()
    try:
        auth = db.get(Authorization, r.json()["registration"]["authorization_id"])
        now = datetime.now(timezone.utc)
        valid_from = auth.valid_from
        if valid_from.tzinfo is None:
            valid_from = valid_from.replace(tzinfo=timezone.utc)
        assert auth.status is AuthorizationStatus.ACTIVE
        assert valid_from <= now
    finally:
        db.close()


def test_intake_links_the_event_to_what_it_just_created(guard, database):
    """El evento tiene que quedar enlazado con la persona, el vehiculo y el
    permiso recien creados.

    `apply_manual_decision` solo reasigna el vehiculo cuando se le pasa una
    placa corregida, asi que al registrar una placa bien leida el evento se
    quedaba permitido pero sin dueno, y los informes por persona se saltaban
    justo las altas de caseta.
    """
    event = _unknown_plate_event(guard, database, "LNK-60-01")
    r = guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Irene Vargas Luna", "institutional_id": "90060",
              "person_type": "staff"},
    )
    assert r.status_code == 200, r.text
    reg = r.json()["registration"]

    ctx = guard.get(f"{API}/events/{event['id']}/context")
    assert ctx.status_code == 200, ctx.text
    body = ctx.json()
    assert body["matched_person"]["full_name"] == "Irene Vargas Luna"
    assert body["matched_vehicle"]["id"] == reg["vehicle_id"]
    assert body["active_authorization"]["permit_code"] == reg["permit_code"]


def test_latest_lane_event_carries_its_context(guard, database):
    """Es lo que la consola consulta para enterarse sola de que paso un coche."""
    event = _unknown_plate_event(guard, database, "LST-70-01")
    assert guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Beatriz Olmos Pardo", "visit_reason": "Visita"},
    ).status_code == 200

    r = guard.get(f"{API}/events/lane/{database['lane_id']}/latest")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event"]["id"] == event["id"]
    assert body["matched_person"]["full_name"] == "Beatriz Olmos Pardo"


def test_a_guard_cannot_read_another_lane(guard, database):
    r = guard.get(f"{API}/events/lane/{database['other_lane_id']}/latest")
    assert r.status_code == 403


# --------------------------------------------------------------------------
# Aviso al guardia cuando no se consigue identificar
# --------------------------------------------------------------------------
def _watcher_with(monkeypatch_cfg: dict, pending: bool = False,
                  reads: bool = True):
    """Un vigilante con la configuracion dada y sin tocar camara ni modelos.

    `pending` simula que el guardia ya tiene un aviso sin resolver.
    `reads`   siembra alguna lectura: sin NINGUNA, el vigilante no avisa a
              proposito, asi que las pruebas sobre las demas reglas necesitan
              que haya al menos una.
    """
    from app.recognition.lane_watcher import LaneWatcher

    w = LaneWatcher()
    w._has_pending_review = lambda cfg: pending  # type: ignore[method-assign]
    if reads:
        w._majority_reading = lambda now, cfg: ("ABC123A", 1.0, 2)  # type: ignore[method-assign]
    w._emitted_calls = []  # type: ignore[attr-defined]
    # **kwargs a proposito: `_emit` ha ganado parametros tres veces
    # (recognition, guess, consistency...) y cada vez el stub se quedo corto y
    # rompio la suite por la firma, no por el comportamiento.
    w._emit = lambda jpeg, cfg, unreadable=False, **kw: (  # type: ignore[method-assign]
        w._emitted_calls.append(unreadable)  # type: ignore[attr-defined]
    )
    return w


def test_no_identification_waits_for_the_streak_then_emits_once():
    """Tiene que avisar, pero no a la primera ni dos veces seguidas."""
    cfg = {"unreadable_after": 3, "unreadable_cooldown": 60.0}
    w = _watcher_with(cfg)
    now = 1000.0
    for _ in range(2):
        w._no_identification(b"x", cfg, now)
    assert w._emitted_calls == [], "no deberia avisar antes de la racha"

    w._no_identification(b"x", cfg, now)
    assert w._emitted_calls == [True], "deberia avisar al cumplirse la racha"


def test_a_parked_vehicle_does_not_flood_the_guard():
    """Un coche quieto delante de la camara no puede avisar cien veces.

    Ya no lo impide ningun reloj -el operador pidio que el aviso salga en
    cuanto se ve una placa, sin esperas- sino la PRESENCIA: tras avisar, el
    carril tiene que quedar despejado antes de volver a avisar.
    """
    cfg = {"unreadable_after": 2, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg)

    now = 1000.0
    for _ in range(2):
        w._no_identification(b"x", cfg, now)
    assert w._emitted_calls == [True]
    assert w._awaiting_clear is True, "tras avisar debe esperar a que se despeje"

    # El coche sigue ahi. Pase el tiempo que pase, no se repite el aviso.
    now += 3600.0
    for _ in range(50):
        w._no_identification(b"x", cfg, now)
    assert w._emitted_calls == [True], "el mismo coche no debe volver a avisar"

    # El vehiculo se va: el carril se da por despejado (lo hace _handle).
    w._awaiting_clear = False
    for _ in range(2):
        w._no_identification(b"x", cfg, now)
    assert w._emitted_calls == [True, True], (
        "despejado el carril, el siguiente vehiculo debe avisar"
    )


def test_a_new_vehicle_prompts_immediately_with_no_time_limit():
    """Sin limites de tiempo: el primer analisis con placa ya avisa."""
    cfg = {"unreadable_after": 1, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg)
    w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == [True], "deberia avisar al primer analisis"


def test_no_identification_can_be_disabled():
    cfg = {"unreadable_after": 0, "unreadable_cooldown": 60.0}
    w = _watcher_with(cfg)
    for _ in range(20):
        w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == []


# --------------------------------------------------------------------------
# Apertura manual de la pluma
# --------------------------------------------------------------------------
def test_manual_open_leaves_a_signed_event(guard, database):
    """Una barrera que se abre sin dejar registro es un agujero.

    El evento va sin placa (no la hay), como ALLOW por decision manual y
    firmado por el guardia, que es lo que de verdad ocurrio.
    """
    r = guard.post(f"{API}/camera/gate/open", json={"notes": "Peaton con paquete"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["lane"]

    detail = guard.get(f"{API}/events/{body['event_id']}").json()
    assert detail["decision"] == "allow"
    assert detail["decision_reason"] == "manual_override"
    assert detail["is_manual"] is True
    assert detail["plate_normalized"] is None
    assert detail["resolved_at"] is not None
    assert detail["decided_by_name"]
    assert "Peaton con paquete" in (detail["notes"] or "")


def test_manual_open_is_audited(guard, admin, database):
    assert guard.post(f"{API}/camera/gate/open", json={}).status_code == 200
    trail = admin.get(f"{API}/audit?page_size=50").json()["items"]
    assert "gate.manual_open" in [r["action"] for r in trail]


def test_manual_open_reports_a_simulated_barrier_honestly(guard, database):
    """En las pruebas el carril usa el driver `mock`, y eso tiene que verse:
    ningun informe debe confundir una simulacion con una pluma que se movio."""
    r = guard.post(f"{API}/camera/gate/open", json={})
    assert r.status_code == 200, r.text
    assert r.json()["actuation_status"] == "simulated"


def test_a_guard_cannot_open_another_lane(guard, database):
    r = guard.post(f"{API}/camera/gate/open",
                   json={"lane_id": database["other_lane_id"]})
    assert r.status_code == 403


def test_auditor_cannot_open_the_barrier(client, database):
    """El auditor es de solo lectura. Levantar una pluma no es leer."""
    from .conftest import sign_in

    sign_in(client, "t_audit")
    assert client.post(f"{API}/camera/gate/open", json={}).status_code == 403


def test_manual_open_on_a_missing_lane_is_a_404(admin, database):
    assert admin.post(f"{API}/camera/gate/open",
                      json={"lane_id": 999999}).status_code == 404


def test_calibration_is_admin_only(guard, database):
    """Buscar topes mecanicos es mantenimiento, no operacion de caseta."""
    assert guard.post(f"{API}/camera/gate/jog",
                      json={"controller_id": 1, "pulse_us": 1500}).status_code == 403
    assert guard.get(f"{API}/camera/gate").status_code == 403


def test_no_second_prompt_while_one_is_unresolved():
    """Si el guardia ya tiene un aviso sin atender, no se le manda otro.

    Esta es la proteccion que sustituyo al temporizador de 60 s. El
    temporizador era justo lo que hacia que el aviso tardara un minuto en
    aparecer con el coche ya delante; lo que de verdad evita inundar la consola
    es que haya uno pendiente, no esperar.
    """
    cfg = {"unreadable_after": 2, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg, pending=True)
    for _ in range(20):
        w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == [], "no debe avisar con uno pendiente"


def test_prompts_again_once_the_guard_resolves_it():
    """Resuelto el anterior, el siguiente vehiculo avisa enseguida."""
    cfg = {"unreadable_after": 2, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg, pending=True)
    for _ in range(4):
        w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == []

    # El guardia lo atiende: ya no hay pendientes.
    w._has_pending_review = lambda cfg: False  # type: ignore[method-assign]
    for _ in range(2):
        w._no_identification(b"x", cfg, 1000.5)
    assert w._emitted_calls == [True], "deberia avisar en cuanto se libera la cola"


def test_an_old_abandoned_pending_does_not_silence_the_lane(guard, database):
    """Un pendiente viejo no puede callar el carril para siempre.

    La primera version de la regla pregunto por CUALQUIER evento sin resolver,
    y con cien eventos viejos acumulados de las pruebas ningun vehiculo nuevo
    habria vuelto a avisar nunca. La ventana es lo que lo arregla.
    """
    from datetime import datetime, timedelta, timezone

    from app.core.db import SessionLocal
    from app.models import AccessEvent, DataOrigin, Decision, DecisionReason, EventSource
    from app.recognition.lane_watcher import LaneWatcher

    # Carril 2 a proposito: las demas pruebas crean eventos en el carril 1 y
    # contaminarian la comprobacion.
    lane_id = database["other_lane_id"]
    db = SessionLocal()
    try:
        # Un pendiente de hace una hora, abandonado.
        db.add(AccessEvent(
            occurred_at=datetime.now(timezone.utc) - timedelta(hours=1),
            lane_id=lane_id, source=EventSource.CAMERA,
            decision=Decision.MANUAL_REVIEW,
            decision_reason=DecisionReason.NO_PLATE_READ,
            origin=DataOrigin.REAL,
        ))
        db.commit()
    finally:
        db.close()

    w = LaneWatcher()
    cfg = {"lane_id": lane_id, "pending_window": 90.0}
    assert w._has_pending_review(cfg) is False, (
        "un pendiente de hace una hora no deberia silenciar el carril"
    )

    # Uno recien creado SI debe silenciar los siguientes avisos.
    db = SessionLocal()
    try:
        db.add(AccessEvent(
            occurred_at=datetime.now(timezone.utc),
            lane_id=lane_id, source=EventSource.CAMERA,
            decision=Decision.MANUAL_REVIEW,
            decision_reason=DecisionReason.NO_PLATE_READ,
            origin=DataOrigin.REAL,
        ))
        db.commit()
    finally:
        db.close()
    assert w._has_pending_review(cfg) is True


def test_no_prompt_at_all_without_a_single_reading():
    """Si no se leyo NADA, no se molesta al guardia.

    Antes se creaba el evento igualmente y el formulario decia "la camara leyo:
    nada legible" con el campo en blanco. Eso no le dice al guardia nada que no
    vea por la ventana, y le llena la cola de eventos que solo puede cerrar a
    mano. Una lectura MALA si vale -es por donde empezar a teclear-; ninguna, no.
    """
    cfg = {"unreadable_after": 1, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg, reads=False)
    for _ in range(20):
        w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == [], "sin ninguna lectura no deberia avisar"


def test_a_bad_reading_is_still_worth_prompting():
    """Una lectura mala si avisa: es por donde el guardia empieza a teclear."""
    cfg = {"unreadable_after": 1, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg)
    w._majority_reading = lambda now, c: ("VTC334A", 0.4, 5)  # type: ignore[method-assign]
    w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == [True]


def test_a_suppressed_prompt_does_not_block_the_next_one():
    """Callarse por falta de lectura no puede bloquear el aviso siguiente.

    La comprobacion de "hay alguna lectura?" estaba DESPUES de marcar el carril
    como esperando despeje, asi que un silencio dejaba el carril bloqueado hasta
    que el vehiculo se fuera: el aviso bueno, cuando por fin llegaba una
    lectura, no salia. Se vio porque `plates_unreadable` marcaba 1 con
    `events_created` en 0.
    """
    cfg = {"unreadable_after": 1, "unreadable_cooldown": 0.0}
    w = _watcher_with(cfg, reads=False)

    # Varios analisis sin ninguna lectura: no avisa y NO toca el estado.
    for _ in range(5):
        w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == []
    assert w._awaiting_clear is False, (
        "un silencio no deberia dejar el carril esperando despeje"
    )
    assert w.status.plates_unreadable == 0, (
        "no deberia contar como ilegible un aviso que nunca salio"
    )

    # Llega una lectura: ahora si debe avisar, sin esperar a nada.
    w._majority_reading = lambda now, c: ("VTC334A", 0.5, 2)  # type: ignore[method-assign]
    w._no_identification(b"x", cfg, 1000.0)
    assert w._emitted_calls == [True], "con lectura deberia avisar enseguida"


# --------------------------------------------------------------------------
# Captura forzada por el guardia
# --------------------------------------------------------------------------
def test_capture_now_is_denied_to_the_auditor(client, database):
    """El auditor es de solo lectura. Forzar una captura puede abrir la barrera."""
    from .conftest import sign_in

    sign_in(client, "t_audit")
    assert client.post(f"{API}/camera/capture-now").status_code == 403


def test_capture_now_respects_the_assigned_lane(guard, database):
    """Se comprueba ANTES de tocar la camara, asi que la prueba no depende de
    que haya uuna conectada."""
    r = guard.post(
        f"{API}/camera/capture-now?lane_id={database['other_lane_id']}"
    )
    assert r.status_code == 403


def test_capture_now_on_a_missing_lane_is_a_404(admin, database):
    assert admin.post(f"{API}/camera/capture-now?lane_id=999999").status_code == 404


# --------------------------------------------------------------------------
# Placas parecidas: no duplicar al mismo coche
# --------------------------------------------------------------------------
def test_edit_distance_is_symmetric_and_bounded():
    from app.services.access import _edit_distance

    assert _edit_distance("FTC224A", "FTC224A") == 0
    assert _edit_distance("FTC224A", "FTC324A") == 1
    assert _edit_distance("FTC224A", "VTC324A") == 2
    # Con corte: lo unico que importa es que supere el umbral.
    assert _edit_distance("FTC224A", "GPT4567", cap=2) > 2
    assert _edit_distance("ABC123A", "CBA123A") == _edit_distance("CBA123A", "ABC123A")


def test_similar_vehicles_finds_the_near_miss(guard, database):
    """El caso real: el OCR lee FTC324A de un coche registrado como FTC224A."""
    from app.core.db import SessionLocal
    from app.services.access import similar_vehicles

    event = _unknown_plate_event(guard, database, "SIM-11-01")
    assert guard.post(
        f"{API}/events/{event['id']}/register-credential",
        json={"full_name": "Nora Pena Soto", "institutional_id": "91101",
              "person_type": "student"},
    ).status_code == 200

    db = SessionLocal()
    try:
        # Una lectura con un caracter distinto.
        hits = similar_vehicles(db, "SIM1121")
        plates = [v.plate for v, _ in hits]
        assert "SIM1101" in plates, f"no encontro la parecida; vio {plates}"
        # Y una placa que no se parece en nada no debe salir.
        assert "SIM1101" not in [v.plate for v, _ in similar_vehicles(db, "ZZZ9999")]
    finally:
        db.close()


def test_an_exact_match_gets_no_suggestions(guard, database):
    """Si el vehiculo esta identificado, sugerir parecidos solo confunde."""
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "GPT-45-67", "lane_id": database["lane_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["matched_vehicle"] is not None
    assert body["similar_vehicles"] == []


def test_suggestions_appear_for_an_unknown_near_miss(guard, database):
    """El guardia tiene que VER que ya existe un registro parecido."""
    event = _unknown_plate_event(guard, database, "NER-55-01")
    assert guard.post(
        f"{API}/events/{event['id']}/register-visitor",
        json={"full_name": "Olga Reyes Mora", "visit_reason": "Visita"},
    ).status_code == 200

    r = guard.post(f"{API}/events/manual",
                   json={"plate": "NER-55-02", "lane_id": database["lane_id"]})
    assert r.status_code == 200, r.text
    sims = r.json()["similar_vehicles"]
    assert any(s["plate"] == "NER5501" for s in sims), f"vio {sims}"
    assert all(s["distance"] <= 2 for s in sims)
