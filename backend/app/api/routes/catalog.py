"""Physical topology: campus, access points, lanes, cameras, controllers."""
from fastapi import APIRouter, HTTPException, Query, Request, status
from sqlalchemy import select

from app.api.deps import AdminOnly, AdminOrSupervisor, AnyRole, DbSession
from app.api.serializers import access_point_out, campus_out, device_out, lane_out
from app.hardware.drivers import (
    BARRIER_DRIVERS, CAMERA_DRIVERS, driver_catalog, get_barrier_driver,
    get_camera_driver,
)
from app.models import AccessPoint, BarrierController, Camera, Campus, Lane
from app.schemas.catalog import (
    AccessPointIn, AccessPointOut, CampusIn, CampusOut, DeviceIn, DeviceOut,
    LaneIn, LaneOut,
)
from app.schemas.common import Message
from app.services import audit

router = APIRouter(prefix="/catalog", tags=["catalog"])


# --- Campus ---------------------------------------------------------------
@router.get("/campuses", response_model=list[CampusOut])
def list_campuses(db: DbSession, user: AnyRole, active_only: bool = False):
    stmt = select(Campus).order_by(Campus.name)
    if active_only:
        stmt = stmt.where(Campus.is_active.is_(True))
    return [campus_out(db, c) for c in db.scalars(stmt)]


@router.post("/campuses", response_model=CampusOut, status_code=status.HTTP_201_CREATED)
def create_campus(payload: CampusIn, request: Request, db: DbSession, user: AdminOnly):
    if db.scalar(select(Campus).where(Campus.code == payload.code)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ya existe un campus con ese código.")
    campus = Campus(**payload.model_dump())
    db.add(campus)
    db.flush()
    audit.record(db, action="catalog.campus_created", actor=user, entity_type="campus",
                 entity_id=campus.id, summary=campus.name, request=request)
    db.commit()
    return campus_out(db, campus)


@router.put("/campuses/{campus_id}", response_model=CampusOut)
def update_campus(campus_id: int, payload: CampusIn, request: Request,
                  db: DbSession, user: AdminOnly):
    campus = db.get(Campus, campus_id)
    if not campus:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Campus no encontrado.")
    for k, v in payload.model_dump().items():
        setattr(campus, k, v)
    audit.record(db, action="catalog.campus_updated", actor=user, entity_type="campus",
                 entity_id=campus.id, summary=campus.name, request=request)
    db.commit()
    return campus_out(db, campus)


# --- Access points --------------------------------------------------------
@router.get("/access-points", response_model=list[AccessPointOut])
def list_access_points(db: DbSession, user: AnyRole, campus_id: int | None = None):
    stmt = select(AccessPoint).order_by(AccessPoint.name)
    if campus_id:
        stmt = stmt.where(AccessPoint.campus_id == campus_id)
    return [access_point_out(db, ap) for ap in db.scalars(stmt)]


@router.post("/access-points", response_model=AccessPointOut,
             status_code=status.HTTP_201_CREATED)
def create_access_point(payload: AccessPointIn, request: Request,
                        db: DbSession, user: AdminOnly):
    if not db.get(Campus, payload.campus_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El campus no existe.")
    ap = AccessPoint(**payload.model_dump())
    db.add(ap)
    db.flush()
    audit.record(db, action="catalog.access_point_created", actor=user,
                 entity_type="access_point", entity_id=ap.id, summary=ap.name,
                 request=request)
    db.commit()
    return access_point_out(db, ap)


@router.put("/access-points/{ap_id}", response_model=AccessPointOut)
def update_access_point(ap_id: int, payload: AccessPointIn, request: Request,
                        db: DbSession, user: AdminOnly):
    ap = db.get(AccessPoint, ap_id)
    if not ap:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Acceso no encontrado.")
    for k, v in payload.model_dump().items():
        setattr(ap, k, v)
    audit.record(db, action="catalog.access_point_updated", actor=user,
                 entity_type="access_point", entity_id=ap.id, request=request)
    db.commit()
    return access_point_out(db, ap)


# --- Lanes ----------------------------------------------------------------
@router.get("/lanes", response_model=list[LaneOut])
def list_lanes(db: DbSession, user: AnyRole, access_point_id: int | None = None,
               active_only: bool = False):
    stmt = select(Lane).order_by(Lane.code)
    if access_point_id:
        stmt = stmt.where(Lane.access_point_id == access_point_id)
    if active_only:
        stmt = stmt.where(Lane.is_active.is_(True))
    return [lane_out(l) for l in db.scalars(stmt)]


@router.post("/lanes", response_model=LaneOut, status_code=status.HTTP_201_CREATED)
def create_lane(payload: LaneIn, request: Request, db: DbSession, user: AdminOnly):
    if not db.get(AccessPoint, payload.access_point_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El acceso no existe.")
    lane = Lane(**payload.model_dump())
    db.add(lane)
    db.flush()
    audit.record(db, action="catalog.lane_created", actor=user, entity_type="lane",
                 entity_id=lane.id, summary=lane.name, request=request)
    db.commit()
    return lane_out(lane)


@router.put("/lanes/{lane_id}", response_model=LaneOut)
def update_lane(lane_id: int, payload: LaneIn, request: Request,
                db: DbSession, user: AdminOnly):
    lane = db.get(Lane, lane_id)
    if not lane:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Carril no encontrado.")
    for k, v in payload.model_dump().items():
        setattr(lane, k, v)
    audit.record(db, action="catalog.lane_updated", actor=user, entity_type="lane",
                 entity_id=lane.id, request=request)
    db.commit()
    return lane_out(lane)


# --- Devices --------------------------------------------------------------
@router.get("/drivers")
def list_drivers(user: AnyRole):
    """What the platform can talk to, and what is only declared so far."""
    return driver_catalog()


@router.get("/cameras", response_model=list[DeviceOut])
def list_cameras(db: DbSession, user: AnyRole, lane_id: int | None = None):
    stmt = select(Camera).order_by(Camera.code)
    if lane_id:
        stmt = stmt.where(Camera.lane_id == lane_id)
    return [device_out(c, is_camera=True) for c in db.scalars(stmt)]


@router.post("/cameras", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_camera(payload: DeviceIn, request: Request, db: DbSession, user: AdminOnly):
    if payload.driver not in CAMERA_DRIVERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Driver de cámara desconocido: {payload.driver}")
    if not db.get(Lane, payload.lane_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El carril no existe.")
    cam = Camera(**payload.model_dump())
    db.add(cam)
    db.flush()
    audit.record(db, action="catalog.camera_created", actor=user, entity_type="camera",
                 entity_id=cam.id, summary=f"{cam.name} ({cam.driver})", request=request)
    db.commit()
    return device_out(cam, is_camera=True)


@router.post("/cameras/{camera_id}/health", response_model=Message)
def check_camera(camera_id: int, db: DbSession, user: AdminOrSupervisor):
    cam = db.get(Camera, camera_id)
    if not cam:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Cámara no encontrada.")
    ok, detail = get_camera_driver(cam.driver).health(cam.config or {})
    from app.models import DeviceStatus
    cam.status = DeviceStatus.ONLINE if ok else DeviceStatus.OFFLINE
    db.commit()
    return Message(detail=detail)


@router.get("/controllers", response_model=list[DeviceOut])
def list_controllers(db: DbSession, user: AnyRole, lane_id: int | None = None):
    stmt = select(BarrierController).order_by(BarrierController.code)
    if lane_id:
        stmt = stmt.where(BarrierController.lane_id == lane_id)
    return [device_out(c, is_camera=False) for c in db.scalars(stmt)]


@router.post("/controllers", response_model=DeviceOut, status_code=status.HTTP_201_CREATED)
def create_controller(payload: DeviceIn, request: Request, db: DbSession, user: AdminOnly):
    if payload.driver not in BARRIER_DRIVERS:
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            f"Driver de barrera desconocido: {payload.driver}")
    if not db.get(Lane, payload.lane_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El carril no existe.")
    ctrl = BarrierController(**payload.model_dump())
    db.add(ctrl)
    db.flush()
    audit.record(db, action="catalog.controller_created", actor=user,
                 entity_type="barrier_controller", entity_id=ctrl.id,
                 summary=f"{ctrl.name} ({ctrl.driver})", request=request)
    db.commit()
    return device_out(ctrl, is_camera=False)


@router.post("/controllers/{controller_id}/health", response_model=Message)
def check_controller(controller_id: int, db: DbSession, user: AdminOrSupervisor):
    ctrl = db.get(BarrierController, controller_id)
    if not ctrl:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Controlador no encontrado.")
    ok, detail = get_barrier_driver(ctrl.driver).health(ctrl.config or {})
    from app.models import DeviceStatus
    ctrl.status = DeviceStatus.ONLINE if ok else DeviceStatus.OFFLINE
    db.commit()
    return Message(detail=detail)
