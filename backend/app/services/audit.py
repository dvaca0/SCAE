"""Append-only audit trail.

Every security-relevant action goes through `record`. The actor's label is
denormalised so the trail stays readable after an account is deleted.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import Request
from sqlalchemy.orm import Session

from app.models import AuditLog, User


def record(
    db: Session,
    *,
    action: str,
    actor: Optional[User] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[Any] = None,
    summary: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    request: Optional[Request] = None,
) -> AuditLog:
    entry = AuditLog(
        at=datetime.now(timezone.utc),
        actor_id=actor.id if actor else None,
        actor_label=f"{actor.full_name} ({actor.username})" if actor else "sistema",
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        summary=summary,
        payload=_redact(payload or {}),
        ip_address=request.client.host if request and request.client else None,
        user_agent=(request.headers.get("user-agent", "")[:255] if request else None),
    )
    db.add(entry)
    return entry


_SENSITIVE = {"password", "new_password", "current_password", "token", "secret",
              "api_key", "authorization", "csrf"}


def _redact(payload: dict[str, Any]) -> dict[str, Any]:
    """Never let a credential reach the audit table."""
    clean: dict[str, Any] = {}
    for key, value in payload.items():
        if any(s in key.lower() for s in _SENSITIVE):
            clean[key] = "***"
        elif isinstance(value, dict):
            clean[key] = _redact(value)
        else:
            clean[key] = value
    return clean
