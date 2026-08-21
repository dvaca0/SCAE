"""Schemas for the physical topology and the people/vehicle catalog."""
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.models.enums import (
    AuthorizationStatus, DataOrigin, DeviceStatus, LaneDirection, PersonType,
)
from app.recognition.plate import normalize_for_storage


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Infrastructure -------------------------------------------------------
class CampusOut(ORMModel):
    id: int
    code: str
    name: str
    city: Optional[str] = None
    is_active: bool
    origin: DataOrigin
    access_point_count: int = 0


class CampusIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    city: Optional[str] = Field(default=None, max_length=80)
    is_active: bool = True


class AccessPointOut(ORMModel):
    id: int
    campus_id: int
    campus_name: Optional[str] = None
    code: str
    name: str
    description: Optional[str] = None
    is_active: bool
    origin: DataOrigin
    lane_count: int = 0


class AccessPointIn(BaseModel):
    campus_id: int
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    description: Optional[str] = None
    is_active: bool = True


class LaneOut(ORMModel):
    id: int
    access_point_id: int
    access_point_name: Optional[str] = None
    campus_name: Optional[str] = None
    code: str
    name: str
    direction: LaneDirection
    is_active: bool
    auto_open_on_allow: bool
    origin: DataOrigin
    camera_count: int = 0
    controller_count: int = 0


class LaneIn(BaseModel):
    access_point_id: int
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    direction: LaneDirection = LaneDirection.ENTRY
    is_active: bool = True
    auto_open_on_allow: bool = True


class DeviceOut(ORMModel):
    id: int
    lane_id: int
    lane_name: Optional[str] = None
    access_point_name: Optional[str] = None
    code: str
    name: str
    driver: str
    config: dict[str, Any] = {}
    status: DeviceStatus
    is_active: bool
    origin: DataOrigin
    driver_implemented: bool = False


class DeviceIn(BaseModel):
    lane_id: int
    code: str = Field(min_length=1, max_length=30)
    name: str = Field(min_length=1, max_length=120)
    driver: str = "mock"
    config: dict[str, Any] = {}
    is_active: bool = True


# --- People ---------------------------------------------------------------
class PersonOut(ORMModel):
    id: int
    institutional_id: Optional[str] = None
    first_name: str
    last_name: str
    full_name: str = ""
    person_type: PersonType
    email: Optional[str] = None
    phone: Optional[str] = None
    faculty: Optional[str] = None
    photo_url: Optional[str] = None
    is_active: bool
    origin: DataOrigin
    vehicle_count: int = 0


class PersonIn(BaseModel):
    institutional_id: Optional[str] = Field(default=None, max_length=32)
    first_name: str = Field(min_length=1, max_length=80)
    last_name: str = Field(min_length=1, max_length=80)
    person_type: PersonType = PersonType.STUDENT
    email: Optional[EmailStr] = None
    phone: Optional[str] = Field(default=None, max_length=40)
    faculty: Optional[str] = Field(default=None, max_length=120)
    is_active: bool = True
    notes: Optional[str] = None


# --- Vehicles -------------------------------------------------------------
class VehicleOut(ORMModel):
    id: int
    plate: str
    plate_display: str
    state_code: Optional[str] = None
    brand: Optional[str] = None
    model: Optional[str] = None
    color: Optional[str] = None
    year: Optional[int] = None
    owner_id: Optional[int] = None
    owner_name: Optional[str] = None
    is_active: bool
    origin: DataOrigin
    active_authorizations: int = 0
    is_watchlisted: bool = False


class VehicleIn(BaseModel):
    plate: str = Field(min_length=4, max_length=20)
    state_code: Optional[str] = Field(default=None, max_length=8)
    brand: Optional[str] = Field(default=None, max_length=60)
    model: Optional[str] = Field(default=None, max_length=60)
    color: Optional[str] = Field(default=None, max_length=40)
    year: Optional[int] = Field(default=None, ge=1950, le=2100)
    owner_id: Optional[int] = None
    is_active: bool = True
    notes: Optional[str] = None

    @field_validator("plate")
    @classmethod
    def _normalize(cls, v: str) -> str:
        normalized = normalize_for_storage(v)
        if len(normalized) < 4:
            raise ValueError("La placa no tiene un formato reconocible.")
        return normalized


# --- Authorizations -------------------------------------------------------
class AuthorizationOut(ORMModel):
    id: int
    vehicle_id: int
    plate_display: Optional[str] = None
    person_id: Optional[int] = None
    person_name: Optional[str] = None
    campus_id: Optional[int] = None
    campus_name: Optional[str] = None
    access_point_id: Optional[int] = None
    access_point_name: Optional[str] = None
    status: AuthorizationStatus
    valid_from: datetime
    valid_until: Optional[datetime] = None
    permit_code: Optional[str] = None
    reason: Optional[str] = None
    origin: DataOrigin
    scope_label: str = ""
    is_currently_valid: bool = False


class AuthorizationIn(BaseModel):
    vehicle_id: int
    person_id: Optional[int] = None
    campus_id: Optional[int] = None
    access_point_id: Optional[int] = None
    status: AuthorizationStatus = AuthorizationStatus.ACTIVE
    valid_from: datetime
    valid_until: Optional[datetime] = None
    permit_code: Optional[str] = Field(default=None, max_length=32)
    reason: Optional[str] = None


class AuthorizationStatusUpdate(BaseModel):
    status: AuthorizationStatus
    reason: Optional[str] = None


# --- Watchlist ------------------------------------------------------------
class WatchlistOut(ORMModel):
    id: int
    plate: str
    plate_display: str = ""
    severity: str
    reason: str
    is_active: bool
    origin: DataOrigin
    created_at: datetime


class WatchlistIn(BaseModel):
    plate: str = Field(min_length=4, max_length=20)
    severity: str = "high"
    reason: str = Field(min_length=3)
    is_active: bool = True

    @field_validator("plate")
    @classmethod
    def _normalize(cls, v: str) -> str:
        return normalize_for_storage(v)
