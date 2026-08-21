"""Physical topology: campus -> access point -> lane -> devices.

The prototype had no concept of place at all: a guard had a free-text
`gate_assigned` string. Modelling the topology is what makes multi-campus,
multi-lane operation possible without another rewrite.
"""
from typing import Optional

from sqlalchemy import Boolean, Enum, Float, ForeignKey, JSON, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base, TimestampMixin
from .enums import DataOrigin, DeviceStatus, LaneDirection


class Campus(Base, TimestampMixin):
    __tablename__ = "campus"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[Optional[str]] = mapped_column(String(80))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    access_points: Mapped[list["AccessPoint"]] = relationship(
        back_populates="campus", cascade="all, delete-orphan"
    )


class AccessPoint(Base, TimestampMixin):
    """A gate / caseta. Authorizations are scoped to access points."""

    __tablename__ = "access_point"

    id: Mapped[int] = mapped_column(primary_key=True)
    campus_id: Mapped[int] = mapped_column(ForeignKey("campus.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[Optional[str]] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    campus: Mapped["Campus"] = relationship(back_populates="access_points")
    lanes: Mapped[list["Lane"]] = relationship(
        back_populates="access_point", cascade="all, delete-orphan"
    )


class Lane(Base, TimestampMixin):
    """A single controlled traffic lane. This is where an event happens."""

    __tablename__ = "lane"

    id: Mapped[int] = mapped_column(primary_key=True)
    access_point_id: Mapped[int] = mapped_column(
        ForeignKey("access_point.id", ondelete="CASCADE"), index=True
    )
    code: Mapped[str] = mapped_column(String(20), index=True)
    name: Mapped[str] = mapped_column(String(120))
    direction: Mapped[LaneDirection] = mapped_column(
        Enum(LaneDirection), default=LaneDirection.ENTRY
    )
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    # When false, an ALLOW decision does not attempt to raise the barrier.
    auto_open_on_allow: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    access_point: Mapped["AccessPoint"] = relationship(back_populates="lanes")
    cameras: Mapped[list["Camera"]] = relationship(
        back_populates="lane", cascade="all, delete-orphan"
    )
    controllers: Mapped[list["BarrierController"]] = relationship(
        back_populates="lane", cascade="all, delete-orphan"
    )


class Camera(Base, TimestampMixin):
    """A capture device.

    `driver` names a CameraDriver implementation; `config` carries whatever
    that driver needs (RTSP URL, UniFi camera id, file path...). No vendor
    field is hard-coded into the schema, so adding UniFi Protect later is a
    new driver plus a config payload — not a migration.
    """

    __tablename__ = "camera"

    id: Mapped[int] = mapped_column(primary_key=True)
    lane_id: Mapped[int] = mapped_column(ForeignKey("lane.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    driver: Mapped[str] = mapped_column(String(40), default="mock")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus), default=DeviceStatus.UNKNOWN)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    lane: Mapped["Lane"] = relationship(back_populates="cameras")


class BarrierController(Base, TimestampMixin):
    """Whatever physically opens the gate: relay board, Pi, PLC, nothing."""

    __tablename__ = "barrier_controller"

    id: Mapped[int] = mapped_column(primary_key=True)
    lane_id: Mapped[int] = mapped_column(ForeignKey("lane.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(30), index=True)
    name: Mapped[str] = mapped_column(String(120))
    driver: Mapped[str] = mapped_column(String(40), default="mock")
    config: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[DeviceStatus] = mapped_column(Enum(DeviceStatus), default=DeviceStatus.UNKNOWN)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    origin: Mapped[DataOrigin] = mapped_column(Enum(DataOrigin), default=DataOrigin.REAL)

    lane: Mapped["Lane"] = relationship(back_populates="controllers")
