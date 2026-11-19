"""Domain enumerations.

These are the vocabulary of the platform. They live in one place so the API,
the decision engine and the frontend all speak the same language.
"""
from enum import StrEnum


class Role(StrEnum):
    """Who someone is inside SCAE, not who they are at the university."""

    ADMIN = "admin"          # full platform control, including users
    SUPERVISOR = "supervisor"  # operations oversight, no user management
    GUARD = "guard"          # lane console only
    AUDITOR = "auditor"      # read-only, including the audit trail


class PersonType(StrEnum):
    STUDENT = "student"
    ACADEMIC = "academic"
    STAFF = "staff"
    VISITOR = "visitor"
    SUPPLIER = "supplier"


class LaneDirection(StrEnum):
    ENTRY = "entry"
    EXIT = "exit"
    BIDIRECTIONAL = "bidirectional"


class DeviceStatus(StrEnum):
    ONLINE = "online"
    OFFLINE = "offline"
    DEGRADED = "degraded"
    UNKNOWN = "unknown"


class AuthorizationStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    REVOKED = "revoked"
    EXPIRED = "expired"


class Decision(StrEnum):
    """The output of the decision engine — deliberately separate from the
    physical actuation, which may fail independently."""

    ALLOW = "allow"
    DENY = "deny"
    MANUAL_REVIEW = "manual_review"


class DecisionReason(StrEnum):
    AUTHORIZED = "authorized"
    NO_PLATE_READ = "no_plate_read"
    LOW_CONFIDENCE = "low_confidence"
    UNKNOWN_VEHICLE = "unknown_vehicle"
    NO_AUTHORIZATION = "no_authorization"
    AUTHORIZATION_SUSPENDED = "authorization_suspended"
    AUTHORIZATION_EXPIRED = "authorization_expired"
    OUT_OF_SCHEDULE = "out_of_schedule"
    WRONG_ACCESS_POINT = "wrong_access_point"
    WATCHLISTED = "watchlisted"
    MANUAL_OVERRIDE = "manual_override"


class EventSource(StrEnum):
    """How the event entered the system."""

    CAMERA = "camera"          # automatic capture at the lane
    MANUAL_UPLOAD = "manual_upload"  # guard uploaded a frame
    MANUAL_ENTRY = "manual_entry"    # guard typed the plate
    SIMULATION = "simulation"        # demo / drill


class ActuationStatus(StrEnum):
    NOT_REQUIRED = "not_required"
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    SIMULATED = "simulated"


class IncidentType(StrEnum):
    INFRACTION = "infraction"
    WATCHLIST_HIT = "watchlist_hit"
    UNAUTHORIZED_ATTEMPT = "unauthorized_attempt"
    TAILGATING = "tailgating"
    DEVICE_FAILURE = "device_failure"
    OTHER = "other"


class IncidentSeverity(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class IncidentStatus(StrEnum):
    OPEN = "open"
    IN_REVIEW = "in_review"
    RESOLVED = "resolved"
    DISMISSED = "dismissed"


class DataOrigin(StrEnum):
    """Marks whether a row is real operational data or demo/seed content.

    The UI surfaces this so nobody mistakes seeded material for university
    records. Every seeded row is DEMO.
    """

    REAL = "real"
    DEMO = "demo"
