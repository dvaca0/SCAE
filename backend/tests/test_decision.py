"""The access decision engine is the safety-critical part of SCAE:
it is what opens a physical barrier. It gets tested first."""
from datetime import datetime, timedelta, timezone

import pytest

from app.models.enums import AuthorizationStatus, Decision, DecisionReason
from app.services.decision import AuthorizationView, DecisionRequest, evaluate

NOW = datetime(2026, 8, 19, 12, 0, tzinfo=timezone.utc)


def auth(**kw) -> AuthorizationView:
    base = dict(
        id=1,
        status=AuthorizationStatus.ACTIVE,
        valid_from=NOW - timedelta(days=30),
        valid_until=NOW + timedelta(days=30),
        campus_id=None,
        access_point_id=None,
        person_id=100,
    )
    base.update(kw)
    return AuthorizationView(**base)


def req(**kw) -> DecisionRequest:
    base = dict(plate="GPT4567", confidence=0.9, at=NOW, campus_id=1,
                access_point_id=1, vehicle_id=10, authorizations=())
    base.update(kw)
    return DecisionRequest(**base)


def test_authorized_vehicle_is_allowed():
    r = evaluate(req(authorizations=(auth(),)))
    assert r.decision is Decision.ALLOW
    assert r.reason is DecisionReason.AUTHORIZED
    assert r.authorization_id == 1
    assert r.person_id == 100


def test_no_plate_goes_to_manual_review_not_denial():
    r = evaluate(req(plate=None))
    assert r.decision is Decision.MANUAL_REVIEW
    assert r.reason is DecisionReason.NO_PLATE_READ


def test_watchlist_overrides_a_valid_permit():
    r = evaluate(req(authorizations=(auth(),), is_watchlisted=True,
                     watchlist_reason="Reporte de seguridad"))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.WATCHLISTED
    assert r.requires_incident


def test_low_confidence_never_auto_decides():
    r = evaluate(req(confidence=0.4, authorizations=(auth(),)))
    assert r.decision is Decision.MANUAL_REVIEW
    assert r.reason is DecisionReason.LOW_CONFIDENCE


def test_unknown_vehicle_is_reviewed_not_denied():
    """Visitors and new students must not be hard-denied at the gate."""
    r = evaluate(req(vehicle_id=None))
    assert r.decision is Decision.MANUAL_REVIEW
    assert r.reason is DecisionReason.UNKNOWN_VEHICLE


def test_registered_vehicle_without_permit_is_denied():
    r = evaluate(req(authorizations=()))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.NO_AUTHORIZATION


def test_suspended_permit_is_denied():
    r = evaluate(req(authorizations=(auth(status=AuthorizationStatus.SUSPENDED),)))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.AUTHORIZATION_SUSPENDED


def test_expired_permit_is_denied():
    r = evaluate(req(authorizations=(auth(valid_until=NOW - timedelta(days=1)),)))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.AUTHORIZATION_EXPIRED


def test_permit_not_yet_valid_is_denied():
    r = evaluate(req(authorizations=(auth(valid_from=NOW + timedelta(days=1)),)))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.AUTHORIZATION_EXPIRED


def test_permit_for_another_gate_is_denied():
    r = evaluate(req(access_point_id=2, authorizations=(auth(access_point_id=1),)))
    assert r.decision is Decision.DENY
    assert r.reason is DecisionReason.WRONG_ACCESS_POINT


def test_open_ended_permit_without_expiry_is_allowed():
    r = evaluate(req(authorizations=(auth(valid_until=None),)))
    assert r.decision is Decision.ALLOW


def test_most_specific_permit_wins_in_the_audit_trail():
    broad = auth(id=1, access_point_id=None, campus_id=None)
    specific = auth(id=2, access_point_id=1, campus_id=1)
    r = evaluate(req(authorizations=(broad, specific)))
    assert r.decision is Decision.ALLOW
    assert r.authorization_id == 2


def test_naive_datetimes_are_treated_as_utc():
    """SQLite returns naive datetimes; the engine must not crash on them."""
    naive = auth(valid_from=datetime(2026, 1, 1), valid_until=datetime(2027, 1, 1))
    r = evaluate(req(authorizations=(naive,)))
    assert r.decision is Decision.ALLOW


@pytest.mark.parametrize("threshold,conf,expected", [
    (0.55, 0.56, Decision.ALLOW),
    (0.55, 0.55, Decision.ALLOW),
    (0.55, 0.54, Decision.MANUAL_REVIEW),
    (0.90, 0.80, Decision.MANUAL_REVIEW),
])
def test_confidence_threshold_boundary(threshold, conf, expected):
    r = evaluate(req(confidence=conf, review_threshold=threshold,
                     authorizations=(auth(),)))
    assert r.decision is expected
