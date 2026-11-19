"""Identity and authorization.

The prototype conflated three different things into `Person.plate`:
who someone is, what they drive, and whether they may enter. Splitting
them is the single most important schema change in v2 — it is what allows
a vehicle to change owner, a permit to expire, and a visitor to be granted
access to one gate for one afternoon.
"""
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean, DateTime, Enum, ForeignKey, Index, String, Text, UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from .enums import AuthorizationStatus, DataOrigin, IncidentSeverity, PersonType, Role


class Person(Base, TimestampMixin):
    """A human known to the university. Not a login account."""

    __tablename__ = "person"

    id: Mapped[int] = mapped_column(primary_key=True)
    # Institutional identifier (número de cuenta / nómina). Unique when present.
    institutional_id: Mapped[Optional[str]] = mapped_column(String(32), unique=True, index=True)
    first_name: Mapped[str] = mapped_column(String(80))
    last_name: Mapped[str] = mapped_column(String(80))
    person_type: Mapped[PersonType] = mapped_column(Enum(PersonType), default=PersonType.STUDENT)
    # The prototype had no email field, which is why infraction mail went to a
    # placeholder address. Fixing that starts here.
    email: Mapped[Optional[str]] = mapped_column(String(160), index=True)
    phone: Mapped[Optional[str]] = mapped_column(String(40))
    faculty: Mapped[Optional[str]] = mapped_column(String(120))
    photo_url: Mapped[Optional[str]] = mapped_column(String(255))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    vehicles: Mapped[list["Vehicle"]] = relationship(back_populates="owner")
    authorizations: Mapped[list["Authorization"]] = relationship(back_populates="person")
    user: Mapped[Optional["User"]] = relationship(back_populates="person", uselist=False)

    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class Vehicle(Base, TimestampMixin):
    """A vehicle identified by its normalized plate.

    `plate` is the normalized form used for matching; `plate_display` keeps
    the human-formatted version for the UI.
    """

    __tablename__ = "vehicle"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(16), unique=True, index=True)
    plate_display: Mapped[str] = mapped_column(String(20))
    state_code: Mapped[Optional[str]] = mapped_column(String(8))  # e.g. COL, JAL
    brand: Mapped[Optional[str]] = mapped_column(String(60))
    model: Mapped[Optional[str]] = mapped_column(String(60))
    color: Mapped[Optional[str]] = mapped_column(String(40))
    year: Mapped[Optional[int]] = mapped_column()
    owner_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    owner: Mapped[Optional["Person"]] = relationship(back_populates="vehicles")
    authorizations: Mapped[list["Authorization"]] = relationship(back_populates="vehicle")


class Authorization(Base, TimestampMixin):
    """Permission for a vehicle to use an access point during a period.

    Scope rules:
      - access_point_id NULL  -> valid at every access point of the campus
      - campus_id NULL        -> valid institution-wide
    Visitor passes are ordinary authorizations with a short validity window,
    which is why there is no separate Visitor table.
    """

    __tablename__ = "authorization"

    id: Mapped[int] = mapped_column(primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicle.id", ondelete="CASCADE"), index=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), index=True
    )
    campus_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("campus.id", ondelete="CASCADE"), index=True
    )
    access_point_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("access_point.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[AuthorizationStatus] = mapped_column(
        Enum(AuthorizationStatus), default=AuthorizationStatus.ACTIVE, index=True
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    valid_until: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), index=True)
    permit_code: Mapped[Optional[str]] = mapped_column(String(32), index=True)
    reason: Mapped[Optional[str]] = mapped_column(Text)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    vehicle: Mapped["Vehicle"] = relationship(back_populates="authorizations")
    person: Mapped[Optional["Person"]] = relationship(back_populates="authorizations")

    __table_args__ = (
        Index("ix_authorization_lookup", "vehicle_id", "status", "valid_from", "valid_until"),
    )


class WatchlistEntry(Base, TimestampMixin):
    """A plate flagged regardless of whether we know the vehicle.

    Kept separate from Vehicle because security needs to flag a plate that
    has never been registered — the prototype could not express that.
    """

    __tablename__ = "watchlist_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    plate: Mapped[str] = mapped_column(String(16), index=True)
    severity: Mapped[IncidentSeverity] = mapped_column(
        Enum(IncidentSeverity), default=IncidentSeverity.HIGH
    )
    reason: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)
    created_by_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("user.id", ondelete="SET NULL")
    )
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    __table_args__ = (UniqueConstraint("plate", name="uq_watchlist_plate"),)


class User(Base, TimestampMixin):
    """A login account. Separate from Person: not every person has a login,
    and a login (integration account) need not be a person."""

    __tablename__ = "user"

    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    email: Mapped[Optional[str]] = mapped_column(String(160), unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(160))
    password_hash: Mapped[str] = mapped_column(String(255))
    role: Mapped[Role] = mapped_column(Enum(Role), default=Role.GUARD, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    person_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("person.id", ondelete="SET NULL"), unique=True
    )
    # Default lane for a guard; the console opens here.
    default_lane_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("lane.id", ondelete="SET NULL")
    )
    last_login_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    person: Mapped[Optional["Person"]] = relationship(back_populates="user")
