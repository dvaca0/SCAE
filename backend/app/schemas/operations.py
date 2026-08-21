"""Schemas for access events, incidents, shifts and the audit trail."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import (
    ActuationStatus, DataOrigin, Decision, DecisionReason, EventSource,
    IncidentSeverity, IncidentStatus, IncidentType,
)


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class DetectionOut(ORMModel):
    id: int
    detector: str
    object_class: Optional[str] = None
    object_confidence: Optional[float] = None
    bbox_x1: Optional[float] = None
    bbox_y1: Optional[float] = None
    bbox_x2: Optional[float] = None
    bbox_y2: Optional[float] = None
    ocr_engine: Optional[str] = None
    ocr_candidates: list[Any] = []


class AccessEventOut(ORMModel):
    id: int
    occurred_at: datetime
    source: EventSource
    lane_id: Optional[int] = None
    lane_name: Optional[str] = None
    access_point_name: Optional[str] = None
    campus_name: Optional[str] = None

    plate_read: Optional[str] = None
    plate_normalized: Optional[str] = None
    plate_display: Optional[str] = None
    plate_confidence: Optional[float] = None

    vehicle_id: Optional[int] = None
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    person_type: Optional[str] = None
    vehicle_label: Optional[str] = None
    authorization_id: Optional[int] = None

    decision: Decision
    decision_reason: DecisionReason
    decision_label: str = ""
    reason_label: str = ""
    is_manual: bool = False
    decided_by_id: Optional[int] = None
    decided_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    notes: Optional[str] = None

    actuation_status: ActuationStatus
    actuation_detail: Optional[str] = None

    has_evidence: bool = False
    processing_ms: Optional[int] = None
    origin: DataOrigin


class AccessEventDetail(AccessEventOut):
    detections: list[DetectionOut] = []
    plate_candidates: list[Any] = []


class ManualDecisionIn(BaseModel):
    """A guard resolving an event the engine sent to manual review, or
    overriding an automatic decision. Both are recorded as manual."""

    decision: Decision
    notes: Optional[str] = Field(default=None, max_length=1000)
    # When the OCR was wrong, the guard supplies the real plate.
    corrected_plate: Optional[str] = Field(default=None, max_length=20)
    open_barrier: bool = True


class ManualEntryIn(BaseModel):
    """Guard types a plate directly — camera down, motorcycle, damaged plate."""

    plate: str = Field(min_length=4, max_length=20)
    lane_id: int
    notes: Optional[str] = Field(default=None, max_length=1000)


class CaptureResponse(BaseModel):
    event: AccessEventDetail
    # Everything the console needs to render the decision without a second call.
    matched_person: Optional[dict[str, Any]] = None
    matched_vehicle: Optional[dict[str, Any]] = None
    active_authorization: Optional[dict[str, Any]] = None
    recognition_backend: str = ""


class IncidentOut(ORMModel):
    id: int
    incident_type: IncidentType
    severity: IncidentSeverity
    status: IncidentStatus
    title: str
    description: Optional[str] = None
    event_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    plate_display: Optional[str] = None
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    lane_id: Optional[int] = None
    lane_name: Optional[str] = None
    reported_by_name: Optional[str] = None
    resolved_by_name: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_notes: Optional[str] = None
    notified_at: Optional[datetime] = None
    notification_error: Optional[str] = None
    created_at: datetime
    origin: DataOrigin


class IncidentIn(BaseModel):
    incident_type: IncidentType = IncidentType.INFRACTION
    severity: IncidentSeverity = IncidentSeverity.MEDIUM
    title: str = Field(min_length=3, max_length=160)
    description: Optional[str] = None
    event_id: Optional[int] = None
    vehicle_id: Optional[int] = None
    person_id: Optional[int] = None
    lane_id: Optional[int] = None


class IncidentUpdate(BaseModel):
    status: Optional[IncidentStatus] = None
    severity: Optional[IncidentSeverity] = None
    resolution_notes: Optional[str] = None


class ShiftOut(ORMModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    lane_id: Optional[int] = None
    lane_name: Optional[str] = None
    started_at: datetime
    ended_at: Optional[datetime] = None
    is_open: bool = False
    duration_minutes: Optional[int] = None


class AuditOut(ORMModel):
    id: int
    at: datetime
    actor_id: Optional[int] = None
    actor_label: str
    action: str
    entity_type: Optional[str] = None
    entity_id: Optional[str] = None
    summary: Optional[str] = None
    ip_address: Optional[str] = None


# --- Dashboard ------------------------------------------------------------
class TrendPoint(BaseModel):
    bucket: str
    allow: int = 0
    deny: int = 0
    manual_review: int = 0


class DashboardStats(BaseModel):
    range_hours: int
    total_events: int
    allowed: int
    denied: int
    manual_review: int
    pending_review: int
    open_incidents: int
    unknown_vehicles: int
    active_shifts: int
    registered_vehicles: int
    active_authorizations: int
    watchlist_active: int
    avg_processing_ms: Optional[float] = None
    trend: list[TrendPoint] = []
    reasons: list[dict[str, Any]] = []
    busiest_lanes: list[dict[str, Any]] = []
    demo_data_present: bool = False


class SystemComponent(BaseModel):
    key: str
    label: str
    status: str          # operational | simulated | not_implemented | degraded
    detail: str = ""


class SystemHealth(BaseModel):
    environment: str
    database: str
    recognition_backend: str
    recognition_ready: bool
    components: list[SystemComponent]
    demo_data_present: bool
    version: str
