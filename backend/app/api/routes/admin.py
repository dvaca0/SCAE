"""User administration and the audit trail."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, EmailStr, Field
from sqlalchemy import func, select

from app.api.deps import AdminOnly, AnyRole, DbSession
from app.api.serializers import audit_out
from app.core.security import hash_password
from app.models import AuditLog, DataOrigin, Lane, Role, User
from app.schemas.common import Message, Page
from app.schemas.operations import AuditOut
from app.services import audit

router = APIRouter(tags=["admin"])


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    full_name: str
    email: str | None = None
    role: Role
    is_active: bool
    default_lane_id: int | None = None
    default_lane_name: str | None = None
    last_login_at: datetime | None = None
    must_change_password: bool = False
    origin: DataOrigin


class UserCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[a-zA-Z0-9._-]+$")
    full_name: str = Field(min_length=3, max_length=160)
    email: EmailStr | None = None
    password: str = Field(min_length=10, max_length=256)
    role: Role = Role.GUARD
    default_lane_id: int | None = None
    is_active: bool = True


class UserUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=3, max_length=160)
    email: EmailStr | None = None
    role: Role | None = None
    default_lane_id: int | None = None
    is_active: bool | None = None


class PasswordReset(BaseModel):
    new_password: str = Field(min_length=10, max_length=256)


def _user_out(db, u: User) -> UserOut:
    lane = db.get(Lane, u.default_lane_id) if u.default_lane_id else None
    return UserOut.model_validate(u).model_copy(update={
        "default_lane_name": lane.name if lane else None
    })


@router.get("/users", response_model=Page[UserOut])
def list_users(db: DbSession, user: AdminOnly, page: int = Query(1, ge=1),
               page_size: int = Query(50, ge=1, le=200)):
    stmt = select(User)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(User.username).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[UserOut](items=[_user_out(db, u) for u in rows], total=total,
                         page=page, page_size=page_size)


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(payload: UserCreate, request: Request, db: DbSession, user: AdminOnly):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status.HTTP_409_CONFLICT, "Ese nombre de usuario ya existe.")
    if payload.default_lane_id and not db.get(Lane, payload.default_lane_id):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "El carril indicado no existe.")

    data = payload.model_dump(exclude={"password"})
    new_user = User(**data, password_hash=hash_password(payload.password),
                    must_change_password=True)
    db.add(new_user)
    db.flush()
    audit.record(db, action="admin.user_created", actor=user, entity_type="user",
                 entity_id=new_user.id,
                 summary=f"{new_user.username} ({new_user.role.value})", request=request)
    db.commit()
    return _user_out(db, new_user)


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, payload: UserUpdate, request: Request,
                db: DbSession, user: AdminOnly):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")

    data = payload.model_dump(exclude_unset=True)
    # An admin must not lock themselves out or drop their own privileges.
    if target.id == user.id:
        if data.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "No puedes desactivar tu propia cuenta.")
        if data.get("role") and data["role"] != Role.ADMIN:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                "No puedes quitarte tu propio rol de administrador.")

    for k, v in data.items():
        setattr(target, k, v)
    audit.record(db, action="admin.user_updated", actor=user, entity_type="user",
                 entity_id=target.id, summary=target.username,
                 payload=data, request=request)
    db.commit()
    return _user_out(db, target)


@router.post("/users/{user_id}/password", response_model=Message)
def reset_password(user_id: int, payload: PasswordReset, request: Request,
                   db: DbSession, user: AdminOnly):
    target = db.get(User, user_id)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Usuario no encontrado.")
    target.password_hash = hash_password(payload.new_password)
    target.must_change_password = True
    audit.record(db, action="admin.password_reset", actor=user, entity_type="user",
                 entity_id=target.id, summary=target.username, request=request)
    db.commit()
    return Message(detail="Contraseña restablecida. El usuario deberá cambiarla al entrar.")


@router.get("/audit", response_model=Page[AuditOut])
def list_audit(
    db: DbSession, user: AnyRole,
    action: str | None = None, entity_type: str | None = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
):
    """Readable by admins, supervisors and auditors — guards cannot see it."""
    if user.role is Role.GUARD:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "Tu rol no tiene acceso a la auditoría.")
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action.contains(action))
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(
        stmt.order_by(AuditLog.at.desc()).offset((page - 1) * page_size).limit(page_size)
    ).all()
    return Page[AuditOut](items=[audit_out(a) for a in rows], total=total,
                          page=page, page_size=page_size)
