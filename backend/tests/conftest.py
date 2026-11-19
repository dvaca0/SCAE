"""Test fixtures.

Each test module gets a throwaway SQLite file and its own FastAPI client, so
the suite never touches the development database.
"""
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_TMP = Path(tempfile.mkdtemp(prefix="scae-tests-"))
os.environ["SCAE_DATABASE_URL"] = f"sqlite:///{_TMP / 'test.db'}"
os.environ["SCAE_SECRET_KEY"] = "test-secret-not-used-anywhere-else"
os.environ["SCAE_EVIDENCE_DIR"] = str(_TMP / "evidence")
# Never load 100 MB of model weights in the test suite.
os.environ["SCAE_RECOGNITION_BACKEND"] = "stub"

from fastapi.testclient import TestClient  # noqa: E402

from app.core.db import SessionLocal, create_all, engine  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    AccessPoint, Authorization, AuthorizationStatus, Base, BarrierController,
    Camera, Campus, Lane, Person, PersonType, Role, User, Vehicle,
)

NOW = datetime.now(timezone.utc)


@pytest.fixture(scope="session", autouse=True)
def database():
    Base.metadata.drop_all(bind=engine)
    create_all()
    db = SessionLocal()
    try:
        campus = Campus(code="TC", name="Campus de prueba", city="Colima")
        db.add(campus); db.flush()
        ap = AccessPoint(campus_id=campus.id, code="TP", name="Acceso de prueba")
        db.add(ap); db.flush()
        lane = Lane(access_point_id=ap.id, code="L1", name="Carril 1")
        other = Lane(access_point_id=ap.id, code="L2", name="Carril 2")
        db.add_all([lane, other]); db.flush()
        db.add(Camera(lane_id=lane.id, code="C1", name="Cámara 1", driver="mock"))
        db.add(BarrierController(lane_id=lane.id, code="B1", name="Barrera 1", driver="mock"))

        person = Person(institutional_id="T0001", first_name="Prueba",
                        last_name="Usuario", person_type=PersonType.STUDENT,
                        email="prueba@example.test")
        db.add(person); db.flush()

        authorized = Vehicle(plate="GPT4567", plate_display="GPT-45-67", owner_id=person.id)
        unauthorized = Vehicle(plate="HXR8821", plate_display="HXR-88-21", owner_id=person.id)
        db.add_all([authorized, unauthorized]); db.flush()
        db.add(Authorization(
            vehicle_id=authorized.id, person_id=person.id, campus_id=campus.id,
            status=AuthorizationStatus.ACTIVE, valid_from=NOW - timedelta(days=1),
            valid_until=NOW + timedelta(days=30), permit_code="T-0001",
        ))

        for username, role, lane_id in [
            ("t_admin", Role.ADMIN, None),
            ("t_super", Role.SUPERVISOR, None),
            ("t_guard", Role.GUARD, lane.id),
            ("t_audit", Role.AUDITOR, None),
        ]:
            db.add(User(username=username, full_name=f"Test {role.value}",
                        role=role, password_hash=hash_password("TestPassword123!"),
                        default_lane_id=lane_id))
        db.commit()
        yield {"lane_id": lane.id, "other_lane_id": other.id,
               "campus_id": campus.id, "access_point_id": ap.id,
               "vehicle_id": authorized.id, "person_id": person.id}
    finally:
        db.close()


@pytest.fixture
def client():
    with TestClient(app) as c:
        yield c


def sign_in(client: TestClient, username: str) -> TestClient:
    """Log in and arm the CSRF header, the way the SPA does."""
    response = client.post("/api/v1/auth/login",
                           json={"username": username, "password": "TestPassword123!"})
    assert response.status_code == 200, response.text
    client.headers["X-CSRF-Token"] = response.json()["csrf_token"]
    return client


def _role_client(username: str):
    """Each role gets its OWN client.

    Sharing one client between two role fixtures would let the second login
    overwrite the first session cookie, and the test would silently run as
    the wrong user.
    """

    @pytest.fixture
    def fixture():
        with TestClient(app) as c:
            yield sign_in(c, username)

    return fixture


admin = _role_client("t_admin")
supervisor = _role_client("t_super")
guard = _role_client("t_guard")
auditor = _role_client("t_audit")
