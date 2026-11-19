"""The access decision engine.

In the prototype, the decision was an `if person:` branch buried inside the
image-upload endpoint, so it could not be tested, reused by a camera feed,
or audited. Here it is a pure function over an explicit input record.

Rules are evaluated in a fixed order, most-restrictive first:

    1. no plate               -> MANUAL_REVIEW  (nothing to decide on)
    2. watchlisted plate      -> DENY           (security overrides everything)
    3. low OCR confidence     -> MANUAL_REVIEW  (do not act on a guess)
    4. unknown vehicle        -> MANUAL_REVIEW  (a human decides; not a denial)
    5. no authorization       -> DENY
    6. suspended / revoked    -> DENY
    7. expired / not yet valid-> DENY
    8. wrong access point     -> DENY
    9. otherwise              -> ALLOW

Note 4: an unregistered plate is *not* a denial. Real gates get visitors,
contractors and new students constantly; sending those to manual review is
what keeps the barrier usable. That is a deliberate policy change from the
prototype, which logged them as unrecognised and did nothing.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, Sequence

from app.models.enums import AuthorizationStatus, Decision, DecisionReason


@dataclass(frozen=True)
class AuthorizationView:
    """Just enough of an Authorization for the engine to reason about."""

    id: int
    status: AuthorizationStatus
    valid_from: datetime
    valid_until: Optional[datetime]
    campus_id: Optional[int]
    access_point_id: Optional[int]
    person_id: Optional[int]


@dataclass(frozen=True)
class DecisionRequest:
    plate: Optional[str]
    confidence: float
    at: datetime
    campus_id: Optional[int] = None
    access_point_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    authorizations: Sequence[AuthorizationView] = ()
    is_watchlisted: bool = False
    watchlist_reason: Optional[str] = None
    review_threshold: float = 0.55


@dataclass(frozen=True)
class DecisionResult:
    decision: Decision
    reason: DecisionReason
    authorization_id: Optional[int] = None
    person_id: Optional[int] = None
    detail: Optional[str] = None

    @property
    def requires_incident(self) -> bool:
        return self.reason in {
            DecisionReason.WATCHLISTED,
            DecisionReason.NO_AUTHORIZATION,
            DecisionReason.AUTHORIZATION_SUSPENDED,
        }


def _as_aware(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; treat those as UTC."""
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _scope_matches(auth: AuthorizationView, req: DecisionRequest) -> bool:
    """A NULL scope means 'anywhere at this level'."""
    if auth.access_point_id is not None:
        return auth.access_point_id == req.access_point_id
    if auth.campus_id is not None:
        return auth.campus_id == req.campus_id
    return True  # institution-wide permit


def evaluate(req: DecisionRequest) -> DecisionResult:
    """Decide whether this vehicle may pass. No I/O, no side effects."""
    now = _as_aware(req.at)

    # 1. Nothing was read.
    if not req.plate:
        return DecisionResult(
            Decision.MANUAL_REVIEW, DecisionReason.NO_PLATE_READ,
            detail="No se reconoció ninguna placa en la imagen.",
        )

    # 2. Security watchlist wins over any permit.
    if req.is_watchlisted:
        return DecisionResult(
            Decision.DENY, DecisionReason.WATCHLISTED,
            detail=req.watchlist_reason or "Placa en lista de control.",
        )

    # 3. Do not automate a decision we are not confident about.
    if req.confidence < req.review_threshold:
        return DecisionResult(
            Decision.MANUAL_REVIEW, DecisionReason.LOW_CONFIDENCE,
            detail=(
                f"Confianza de lectura {req.confidence:.0%}, por debajo del "
                f"umbral {req.review_threshold:.0%}."
            ),
        )

    # 4. Plate read cleanly but we have never seen this vehicle.
    if req.vehicle_id is None:
        return DecisionResult(
            Decision.MANUAL_REVIEW, DecisionReason.UNKNOWN_VEHICLE,
            detail="Vehículo no registrado. Requiere validación del guardia.",
        )

    in_scope = [a for a in req.authorizations if _scope_matches(a, req)]

    # 8. Registered, has permits, but none for this gate.
    if req.authorizations and not in_scope:
        return DecisionResult(
            Decision.DENY, DecisionReason.WRONG_ACCESS_POINT,
            detail="El permiso no cubre este acceso.",
        )

    # 5. Registered but never authorized.
    if not in_scope:
        return DecisionResult(
            Decision.DENY, DecisionReason.NO_AUTHORIZATION,
            detail="El vehículo no tiene un permiso vigente.",
        )

    active = [a for a in in_scope if a.status == AuthorizationStatus.ACTIVE]

    # 6. Every applicable permit was suspended or revoked.
    if not active:
        worst = in_scope[0]
        reason = (
            DecisionReason.AUTHORIZATION_SUSPENDED
            if worst.status == AuthorizationStatus.SUSPENDED
            else DecisionReason.NO_AUTHORIZATION
        )
        return DecisionResult(
            Decision.DENY, reason, authorization_id=worst.id, person_id=worst.person_id,
            detail=f"Permiso en estado '{worst.status.value}'.",
        )

    # 7. Validity window.
    valid_now = [
        a for a in active
        if _as_aware(a.valid_from) <= now
        and (a.valid_until is None or _as_aware(a.valid_until) >= now)
    ]
    if not valid_now:
        candidate = active[0]
        return DecisionResult(
            Decision.DENY, DecisionReason.AUTHORIZATION_EXPIRED,
            authorization_id=candidate.id, person_id=candidate.person_id,
            detail="El permiso está fuera de su periodo de vigencia.",
        )

    # 9. Prefer the most specific permit, so the audit trail names the one a
    # human would point at.
    valid_now.sort(
        key=lambda a: (a.access_point_id is not None, a.campus_id is not None),
        reverse=True,
    )
    granted = valid_now[0]
    return DecisionResult(
        Decision.ALLOW, DecisionReason.AUTHORIZED,
        authorization_id=granted.id, person_id=granted.person_id,
        detail="Acceso autorizado.",
    )
