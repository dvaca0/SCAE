"""Operational records: access events, detections, incidents, shifts, audit.

`AccessEvent` is the spine of the platform. Everything that happens at a
lane produces exactly one event, and every event carries the full chain:
what was seen, what was read, who it matched, what was decided, why, and
whether the barrier actually moved.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, Float, ForeignKey, Index, Integer, JSON, String, Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from .enums import (
    ActuationStatus, DataOrigin, Decision, DecisionReason, EventSource,
    IncidentSeverity, IncidentStatus, IncidentType,
)


class AccessEvent(Base, TimestampMixin):
    __tablename__ = "access_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    lane_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lane.id", ondelete="SET NULL"), index=True
    )
    camera_id: Mapped[Optional[int]] = mapped_column(ForeignKey("camera.id", ondelete="SET NULL"))
    source: Mapped[EventSource] = mapped_column(Enum(EventSource), default=EventSource.CAMERA)

    # --- what was read ---------------------------------------------------
    plate_read: Mapped[Optional[str]] = mapped_column(String(32))       # raw OCR text
    plate_normalized: Mapped[Optional[str]] = mapped_column(String(16), index=True)
    plate_confidence: Mapped[Optional[float]] = mapped_column(Float)

    # --- who it matched --------------------------------------------------
    vehicle_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("vehicle.id", ondelete="SET NULL"), index=True
    )
    person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"))
    authorization_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("authorization.id", ondelete="SET NULL")
    )

    # --- what was decided ------------------------------------------------
    decision: Mapped[Decision] = mapped_column(Enum(Decision), index=True)
    decision_reason: Mapped[DecisionReason] = mapped_column(Enum(DecisionReason), index=True)
    decided_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    # True when a human overrode (or supplied) the automatic decision.
    is_manual: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notes: Mapped[Optional[str]] = mapped_column(Text)

    # --- what physically happened ---------------------------------------
    actuation_status: Mapped[ActuationStatus] = mapped_column(
        Enum(ActuationStatus), default=ActuationStatus.NOT_REQUIRED
    )
    actuation_detail: Mapped[Optional[str]] = mapped_column(String(255))

    # --- evidence --------------------------------------------------------
    evidence_path: Mapped[Optional[str]] = mapped_column(String(255))
    processing_ms: Mapped[Optional[int]] = mapped_column(Integer)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    detections: Mapped[list["Detection"]] = relationship(
        back_populates="event", cascade="all, delete-orphan"
    )
    incidents: Mapped[list["Incident"]] = relationship(back_populates="event")

    __table_args__ = (
        Index("ix_event_timeline", "occurred_at", "decision"),
        Index("ix_event_lane_time", "lane_id", "occurred_at"),
    )


class Detection(Base, TimestampMixin):
    """Raw computer-vision output for one event.

    Stored separately from the event so the recognition pipeline can be
    swapped, re-run, or benchmarked against historical evidence without
    touching the access record.
    """

    __tablename__ = "detection"

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[int] = mapped_column(
        ForeignKey("access_event.id", ondelete="CASCADE"), index=True
    )
    detector: Mapped[str] = mapped_column(String(60))
    object_class: Mapped[Optional[str]] = mapped_column(String(40))
    object_confidence: Mapped[Optional[float]] = mapped_column(Float)
    bbox_x1: Mapped[Optional[float]] = mapped_column(Float)
    bbox_y1: Mapped[Optional[float]] = mapped_column(Float)
    bbox_x2: Mapped[Optional[float]] = mapped_column(Float)
    bbox_y2: Mapped[Optional[float]] = mapped_column(Float)
    ocr_engine: Mapped[Optional[str]] = mapped_column(String(60))
    # Every candidate the OCR produced, ranked — not just the winner. This is
    # what makes manual review useful and future model comparison possible.
    ocr_candidates: Mapped[list] = mapped_column(JSON, default=list)

    event: Mapped["AccessEvent"] = relationship(back_populates="detections")


class Incident(Base, TimestampMixin):
    __tablename__ = "incident"

    id: Mapped[int] = mapped_column(primary_key=True)
    incident_type: Mapped[IncidentType] = mapped_column(Enum(IncidentType), index=True)
    severity: Mapped[IncidentSeverity] = mapped_column(
        Enum(IncidentSeverity), default=IncidentSeverity.MEDIUM, index=True
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(IncidentStatus), default=IncidentStatus.OPEN, index=True
    )
    title: Mapped[str] = mapped_column(String(160))
    description: Mapped[Optional[str]] = mapped_column(Text)
    event_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("access_event.id", ondelete="SET NULL"), index=True
    )
    vehicle_id: Mapped[Optional[int]] = mapped_column(ForeignKey("vehicle.id", ondelete="SET NULL"))
    person_id: Mapped[Optional[int]] = mapped_column(ForeignKey("person.id", ondelete="SET NULL"))
    lane_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lane.id", ondelete="SET NULL"))
    reported_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    resolved_by_id: Mapped[Optional[int]] = mapped_column(ForeignKey("user.id", ondelete="SET NULL"))
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    resolution_notes: Mapped[Optional[str]] = mapped_column(Text)
    # Whether a notification was actually delivered — never assumed.
    notified_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    notification_error: Mapped[Optional[str]] = mapped_column(String(255))
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    event: Mapped[Optional["AccessEvent"]] = relationship(back_populates="incidents")


class GuardShift(Base, TimestampMixin):
    """Replaces the prototype's GuardLog sign_in/sign_out row pairs with a
    single interval, which is what every report actually wants."""

    __tablename__ = "guard_shift"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("user.id", ondelete="CASCADE"), index=True)
    lane_id: Mapped[Optional[int]] = mapped_column(ForeignKey("lane.id", ondelete="SET NULL"))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)


class AuditLog(Base):
    """Append-only trail of security-relevant actions.

    Deliberately not a TimestampMixin: audit rows are never updated.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    actor_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL"), index=True
    )
    actor_label: Mapped[str] = mapped_column(String(120))  # kept if the user is deleted
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[Optional[str]] = mapped_column(String(60), index=True)
    entity_id: Mapped[Optional[str]] = mapped_column(String(40))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
