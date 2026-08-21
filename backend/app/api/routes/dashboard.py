"""Aggregated views: operational dashboard and system health."""
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query
from sqlalchemy import func, select

from app.api.deps import AnyRole, DbSession
from app.core.config import get_settings
from app.core.db import engine
from app.hardware.drivers import (
    IMPLEMENTED_BARRIER_DRIVERS, IMPLEMENTED_CAMERA_DRIVERS,
)
from app.models import (
    AccessEvent, Authorization, AuthorizationStatus, BarrierController, Camera,
    Campus, DataOrigin, Decision, GuardShift, Incident, IncidentStatus, Lane,
    Person, Vehicle, WatchlistEntry,
)
from app.schemas.operations import (
    DashboardStats, SystemComponent, SystemHealth, TrendPoint,
)
from app.services.access import REASON_LABELS
from app.services.notifications import smtp_configured

router = APIRouter(tags=["dashboard"])
settings = get_settings()
VERSION = "2.0.0"


def _demo_present(db) -> bool:
    return bool(db.scalar(
        select(func.count(Vehicle.id)).where(Vehicle.origin == DataOrigin.DEMO)
    ))


@router.get("/dashboard", response_model=DashboardStats)
def dashboard(db: DbSession, user: AnyRole, hours: int = Query(24, ge=1, le=24 * 30)):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    events = db.scalars(
        select(AccessEvent).where(AccessEvent.occurred_at >= since)
    ).all()

    by_decision = Counter(e.decision for e in events)
    processing = [e.processing_ms for e in events if e.processing_ms]

    # Trend buckets: hourly for a day or less, daily beyond that.
    hourly = hours <= 48
    buckets: dict[str, Counter] = defaultdict(Counter)
    for e in events:
        at = e.occurred_at if e.occurred_at.tzinfo else e.occurred_at.replace(tzinfo=timezone.utc)
        key = at.strftime("%Y-%m-%dT%H:00" if hourly else "%Y-%m-%d")
        buckets[key][e.decision] += 1

    trend = [
        TrendPoint(
            bucket=key,
            allow=counts[Decision.ALLOW],
            deny=counts[Decision.DENY],
            manual_review=counts[Decision.MANUAL_REVIEW],
        )
        for key, counts in sorted(buckets.items())
    ]

    reason_counts = Counter(e.decision_reason for e in events)
    reasons = [
        {"key": r.value, "label": REASON_LABELS.get(r, r.value), "count": c}
        for r, c in reason_counts.most_common(6)
    ]

    lane_counts = Counter(e.lane_id for e in events if e.lane_id)
    busiest = []
    for lane_id, count in lane_counts.most_common(5):
        lane = db.get(Lane, lane_id)
        if lane:
            busiest.append({
                "lane_id": lane_id, "lane": lane.name,
                "access_point": lane.access_point.name if lane.access_point else None,
                "count": count,
            })

    pending = db.scalar(select(func.count(AccessEvent.id)).where(
        AccessEvent.decision == Decision.MANUAL_REVIEW,
        AccessEvent.resolved_at.is_(None),
    )) or 0

    return DashboardStats(
        range_hours=hours,
        total_events=len(events),
        allowed=by_decision[Decision.ALLOW],
        denied=by_decision[Decision.DENY],
        manual_review=by_decision[Decision.MANUAL_REVIEW],
        pending_review=pending,
        open_incidents=db.scalar(select(func.count(Incident.id)).where(
            Incident.status.in_([IncidentStatus.OPEN, IncidentStatus.IN_REVIEW])
        )) or 0,
        unknown_vehicles=sum(
            1 for e in events if e.vehicle_id is None and e.plate_normalized
        ),
        active_shifts=db.scalar(select(func.count(GuardShift.id)).where(
            GuardShift.ended_at.is_(None)
        )) or 0,
        registered_vehicles=db.scalar(select(func.count(Vehicle.id))) or 0,
        active_authorizations=db.scalar(select(func.count(Authorization.id)).where(
            Authorization.status == AuthorizationStatus.ACTIVE
        )) or 0,
        watchlist_active=db.scalar(select(func.count(WatchlistEntry.id)).where(
            WatchlistEntry.is_active.is_(True)
        )) or 0,
        avg_processing_ms=round(sum(processing) / len(processing), 1) if processing else None,
        trend=trend,
        reasons=reasons,
        busiest_lanes=busiest,
        demo_data_present=_demo_present(db),
    )


@router.get("/system/health", response_model=SystemHealth)
def system_health(db: DbSession, user: AnyRole):
    """An honest status board: what really works, what is simulated, and
    what is only declared. Nothing here reports green by default."""
    components: list[SystemComponent] = []

    try:
        db.execute(select(func.count(Campus.id)))
        components.append(SystemComponent(
            key="database", label="Base de datos", status="operational",
            detail=f"{engine.dialect.name} · conexión activa",
        ))
        db_status = "ok"
    except Exception as exc:  # pragma: no cover
        components.append(SystemComponent(
            key="database", label="Base de datos", status="degraded", detail=str(exc)[:120],
        ))
        db_status = "error"

    if settings.recognition_backend == "stub":
        components.append(SystemComponent(
            key="recognition", label="Reconocimiento LPR", status="simulated",
            detail="Backend stub: no se ejecutan modelos de visión.",
        ))
        recognition_ready = False
    else:
        try:
            import easyocr  # noqa: F401
            import ultralytics  # noqa: F401
            recognition_ready = True
            components.append(SystemComponent(
                key="recognition", label="Reconocimiento LPR", status="operational",
                detail=f"YOLO ({settings.detector_model}) + EasyOCR · CPU",
            ))
        except ImportError:
            recognition_ready = False
            components.append(SystemComponent(
                key="recognition", label="Reconocimiento LPR", status="degraded",
                detail="Dependencias de visión no disponibles.",
            ))

    cameras = db.scalars(select(Camera)).all()
    real_cameras = [c for c in cameras if c.driver not in ("mock",)]
    components.append(SystemComponent(
        key="cameras", label="Cámaras",
        status="simulated" if not real_cameras else "operational",
        detail=(
            f"{len(cameras)} registradas · ninguna con hardware real conectado"
            if not real_cameras
            else f"{len(real_cameras)} con driver de hardware"
        ),
    ))

    controllers = db.scalars(select(BarrierController)).all()
    real_ctrl = [c for c in controllers if c.driver not in ("mock",)]
    components.append(SystemComponent(
        key="barriers", label="Barreras",
        status="simulated" if not real_ctrl else "operational",
        detail=(
            f"{len(controllers)} registradas · apertura simulada"
            if not real_ctrl
            else f"{len(real_ctrl)} con controlador físico"
        ),
    ))

    components.append(SystemComponent(
        key="notifications", label="Notificaciones por correo",
        status="operational" if smtp_configured() else "not_implemented",
        detail=(
            "SMTP configurado" if smtp_configured()
            else "SMTP sin configurar: las incidencias no envían correo."
        ),
    ))

    return SystemHealth(
        environment=settings.environment,
        database=db_status,
        recognition_backend=settings.recognition_backend,
        recognition_ready=recognition_ready,
        components=components,
        demo_data_present=_demo_present(db),
        version=VERSION,
    )
