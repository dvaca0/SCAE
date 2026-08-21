"""API-level tests: authentication, role enforcement, CSRF, and the access flow."""
from datetime import datetime, timedelta, timezone

import pytest

API = "/api/v1"


# --- Authentication -------------------------------------------------------
def test_health_is_public(client):
    assert client.get("/api/health").status_code == 200


def test_protected_route_requires_a_session(client):
    assert client.get(f"{API}/dashboard").status_code == 401


def test_login_rejects_a_wrong_password(client):
    r = client.post(f"{API}/auth/login", json={"username": "t_admin", "password": "nope"})
    assert r.status_code == 401


def test_login_does_not_reveal_whether_the_user_exists(client):
    unknown = client.post(f"{API}/auth/login", json={"username": "ghost", "password": "x"})
    wrong = client.post(f"{API}/auth/login", json={"username": "t_admin", "password": "x"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_session_cookie_is_httponly(client):
    r = client.post(f"{API}/auth/login",
                    json={"username": "t_admin", "password": "TestPassword123!"})
    cookie_header = r.headers.get("set-cookie", "")
    assert "scae_session" in cookie_header
    assert "httponly" in cookie_header.lower()


def test_me_returns_the_signed_in_user(admin):
    body = admin.get(f"{API}/auth/me").json()
    assert body["username"] == "t_admin"
    assert body["role"] == "admin"


# --- CSRF -----------------------------------------------------------------
def test_mutation_without_csrf_header_is_rejected(admin):
    del admin.headers["X-CSRF-Token"]
    r = admin.post(f"{API}/watchlist", json={"plate": "AAA1111", "reason": "prueba"})
    assert r.status_code == 403


def test_mutation_with_a_wrong_csrf_token_is_rejected(admin):
    admin.headers["X-CSRF-Token"] = "not-the-real-token"
    r = admin.post(f"{API}/watchlist", json={"plate": "AAA1111", "reason": "prueba"})
    assert r.status_code == 403


def test_reads_do_not_require_csrf(admin):
    del admin.headers["X-CSRF-Token"]
    assert admin.get(f"{API}/dashboard").status_code == 200


# --- Role enforcement -----------------------------------------------------
@pytest.mark.parametrize("path", ["/users", "/audit"])
def test_guard_cannot_read_administrative_endpoints(guard, path):
    assert guard.get(f"{API}{path}").status_code == 403


def test_guard_cannot_create_infrastructure(guard):
    r = guard.post(f"{API}/catalog/campuses", json={"code": "X1", "name": "X"})
    assert r.status_code == 403


def test_auditor_can_read_the_audit_trail(auditor):
    assert auditor.get(f"{API}/audit").status_code == 200


def test_auditor_cannot_write(auditor):
    r = auditor.post(f"{API}/watchlist", json={"plate": "BBB2222", "reason": "prueba"})
    assert r.status_code == 403


def test_supervisor_can_manage_the_registry_but_not_users(supervisor):
    assert supervisor.get(f"{API}/vehicles").status_code == 200
    assert supervisor.get(f"{API}/users").status_code == 403


def test_admin_cannot_deactivate_their_own_account(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.patch(f"{API}/users/{me['id']}", json={"is_active": False})
    assert r.status_code == 400


def test_admin_cannot_drop_their_own_admin_role(admin):
    me = admin.get(f"{API}/auth/me").json()
    r = admin.patch(f"{API}/users/{me['id']}", json={"role": "guard"})
    assert r.status_code == 400


# --- Access flow ----------------------------------------------------------
def test_manual_entry_of_an_authorized_plate_is_allowed(guard, database):
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "GPT-45-67", "lane_id": database["lane_id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["event"]["decision"] == "allow"
    assert body["event"]["decision_reason"] == "authorized"
    assert body["matched_person"]["institutional_id"] == "T0001"
    assert body["active_authorization"]["permit_code"] == "T-0001"
    # The mock barrier must never claim a real opening.
    assert body["event"]["actuation_status"] == "simulated"


def test_registered_vehicle_without_a_permit_is_denied(guard, database):
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "HXR-88-21", "lane_id": database["lane_id"]})
    body = r.json()
    assert body["event"]["decision"] == "deny"
    assert body["event"]["decision_reason"] == "no_authorization"
    assert body["event"]["actuation_status"] == "not_required"


def test_unknown_plate_goes_to_manual_review(guard, database):
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "ZZZ9911", "lane_id": database["lane_id"]})
    body = r.json()
    assert body["event"]["decision"] == "manual_review"
    assert body["event"]["decision_reason"] == "unknown_vehicle"


def test_plate_is_normalized_on_the_way_in(guard, database):
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "  gpt 45 67 ", "lane_id": database["lane_id"]})
    assert r.json()["event"]["plate_normalized"] == "GPT4567"


def test_guard_cannot_operate_another_lane(guard, database):
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "GPT4567", "lane_id": database["other_lane_id"]})
    assert r.status_code == 403


def test_watchlisted_plate_is_denied_and_opens_an_incident(admin, guard, database):
    admin.post(f"{API}/watchlist", json={"plate": "TZK9911", "reason": "Prueba de seguridad"})
    r = guard.post(f"{API}/events/manual",
                   json={"plate": "TZK9911", "lane_id": database["lane_id"]})
    body = r.json()
    assert body["event"]["decision"] == "deny"
    assert body["event"]["decision_reason"] == "watchlisted"

    incidents = admin.get(f"{API}/incidents").json()["items"]
    assert any(i["incident_type"] == "watchlist_hit" for i in incidents)


def test_manual_resolution_records_who_decided(guard, database):
    created = guard.post(f"{API}/events/manual",
                         json={"plate": "QQQ7788", "lane_id": database["lane_id"]}).json()
    event_id = created["event"]["id"]
    r = guard.post(f"{API}/events/{event_id}/decision",
                   json={"decision": "allow", "notes": "Proveedor verificado"})
    assert r.status_code == 200
    resolved = r.json()
    assert resolved["decision"] == "allow"
    assert resolved["is_manual"] is True
    assert resolved["decided_by_name"] == "Test guard"
    assert resolved["resolved_at"] is not None


def test_an_event_cannot_be_resolved_back_into_review(guard, database):
    created = guard.post(f"{API}/events/manual",
                         json={"plate": "RRR1122", "lane_id": database["lane_id"]}).json()
    r = guard.post(f"{API}/events/{created['event']['id']}/decision",
                   json={"decision": "manual_review"})
    assert r.status_code == 400


# --- Upload validation ----------------------------------------------------
def test_non_image_upload_is_rejected(guard):
    r = guard.post(f"{API}/events/capture",
                   files={"image": ("x.txt", b"hello", "text/plain")})
    assert r.status_code == 415


def test_empty_upload_is_rejected(guard):
    r = guard.post(f"{API}/events/capture",
                   files={"image": ("x.jpg", b"", "image/jpeg")})
    assert r.status_code == 400


# --- Registry -------------------------------------------------------------
def test_vehicle_plate_must_be_plausible(supervisor):
    r = supervisor.post(f"{API}/vehicles", json={"plate": "??"})
    assert r.status_code == 422


def test_duplicate_plate_is_rejected(supervisor):
    assert supervisor.post(f"{API}/vehicles", json={"plate": "KKK4455"}).status_code == 201
    assert supervisor.post(f"{API}/vehicles", json={"plate": "KKK-44-55"}).status_code == 409


def test_authorization_end_date_must_follow_the_start(supervisor, database):
    now = datetime.now(timezone.utc)
    r = supervisor.post(f"{API}/authorizations", json={
        "vehicle_id": database["vehicle_id"],
        "valid_from": now.isoformat(),
        "valid_until": (now - timedelta(days=1)).isoformat(),
    })
    assert r.status_code == 400


def test_removing_a_watchlist_entry_deactivates_rather_than_deletes(admin):
    created = admin.post(f"{API}/watchlist",
                         json={"plate": "WWW3344", "reason": "Prueba"}).json()
    assert admin.delete(f"{API}/watchlist/{created['id']}").status_code == 200
    active = admin.get(f"{API}/watchlist?active_only=true").json()
    assert all(w["id"] != created["id"] for w in active)
    every = admin.get(f"{API}/watchlist?active_only=false").json()
    assert any(w["id"] == created["id"] for w in every)


# --- Audit ----------------------------------------------------------------
def test_login_is_audited(admin):
    entries = admin.get(f"{API}/audit?action=auth.login").json()["items"]
    assert any(e["action"] == "auth.login" for e in entries)


def test_failed_login_is_audited(client, admin):
    client.post(f"{API}/auth/login", json={"username": "t_admin", "password": "wrong"})
    entries = admin.get(f"{API}/audit?action=auth.login_failed").json()["items"]
    assert entries


def test_audit_never_stores_a_password(admin):
    entries = admin.get(f"{API}/audit?page_size=200").json()["items"]
    blob = str(entries).lower()
    assert "testpassword123" not in blob


# --- System ---------------------------------------------------------------
def test_system_health_reports_simulated_hardware_honestly(admin):
    body = admin.get(f"{API}/system/health").json()
    by_key = {c["key"]: c for c in body["components"]}
    assert by_key["cameras"]["status"] == "simulated"
    assert by_key["barriers"]["status"] == "simulated"
    assert by_key["notifications"]["status"] == "not_implemented"


def test_driver_catalog_marks_unimplemented_drivers(admin):
    catalog = admin.get(f"{API}/catalog/drivers").json()
    kinds = {d["kind"]: d["implemented"] for d in catalog["cameras"]}
    assert kinds["mock"] is True
    assert kinds["unifi_protect"] is False
    assert kinds["rtsp"] is False


def test_logout_clears_the_session(admin):
    assert admin.post(f"{API}/auth/logout").status_code == 200
    assert admin.get(f"{API}/auth/me").status_code == 401
