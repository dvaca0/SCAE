"""Authentication: login, logout, session identity, password change."""
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, client_ip
from app.core.config import get_settings
from app.core.security import (
    create_access_token, generate_csrf_token, hash_password, needs_rehash,
    verify_password,
)
from app.models import GuardShift, Lane, Role, User
from app.schemas.auth import (
    CurrentUser as CurrentUserOut, LaneRef, LoginRequest, LoginResponse,
    PasswordChange,
)
from app.schemas.common import Message
from app.services import audit

router = APIRouter(prefix="/auth", tags=["auth"])
settings = get_settings()


def _lane_ref(db, lane_id: int | None) -> LaneRef | None:
    if not lane_id:
        return None
    lane = db.get(Lane, lane_id)
    if not lane:
        return None
    return LaneRef(
        id=lane.id, code=lane.code, name=lane.name,
        access_point_name=lane.access_point.name if lane.access_point else None,
        campus_name=(
            lane.access_point.campus.name
            if lane.access_point and lane.access_point.campus else None
        ),
    )


def _user_out(db, user: User) -> CurrentUserOut:
    return CurrentUserOut(
        id=user.id, username=user.username, full_name=user.full_name,
        email=user.email, role=user.role,
        must_change_password=user.must_change_password,
        default_lane=_lane_ref(db, user.default_lane_id),
        last_login_at=user.last_login_at,
    )


def _set_session_cookies(response: Response, token: str, csrf: str) -> None:
    response.set_cookie(
        settings.session_cookie_name, token,
        httponly=True,               # unreadable from JS: XSS cannot steal it
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )
    response.set_cookie(
        settings.csrf_cookie_name, csrf,
        httponly=False,              # deliberately readable: JS echoes it back
        secure=settings.cookie_secure,
        samesite="lax",
        max_age=settings.access_token_ttl_minutes * 60,
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response, db: DbSession):
    user = db.scalar(select(User).where(User.username == payload.username))

    # Same message and roughly the same work for unknown user and wrong
    # password, so the endpoint does not enumerate accounts.
    if user is None or not verify_password(payload.password, user.password_hash):
        audit.record(
            db, action="auth.login_failed", entity_type="user",
            entity_id=payload.username, summary="Credenciales inválidas",
            request=request,
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Usuario o contraseña incorrectos.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="La cuenta está desactivada. Contacta al administrador.",
        )

    # Transparently upgrade the stored hash if Argon2 parameters changed.
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(payload.password)

    user.last_login_at = datetime.now(timezone.utc)

    # A guard logging in opens a shift at their assigned lane.
    if user.role is Role.GUARD and user.default_lane_id:
        open_shift = db.scalar(
            select(GuardShift).where(
                GuardShift.user_id == user.id, GuardShift.ended_at.is_(None)
            )
        )
        if open_shift is None:
            db.add(GuardShift(
                user_id=user.id, lane_id=user.default_lane_id,
                started_at=datetime.now(timezone.utc),
            ))

    token = create_access_token(str(user.id), user.role.value)
    csrf = generate_csrf_token()
    _set_session_cookies(response, token, csrf)

    audit.record(
        db, action="auth.login", actor=user, entity_type="user",
        entity_id=user.id, summary=f"Inicio de sesión ({user.role.value})",
        request=request,
    )
    db.commit()
    return LoginResponse(user=_user_out(db, user), csrf_token=csrf)


@router.post("/logout", response_model=Message)
def logout(request: Request, response: Response, db: DbSession, user: CurrentUser):
    open_shift = db.scalar(
        select(GuardShift).where(
            GuardShift.user_id == user.id, GuardShift.ended_at.is_(None)
        )
    )
    if open_shift:
        open_shift.ended_at = datetime.now(timezone.utc)

    audit.record(db, action="auth.logout", actor=user, entity_type="user",
                 entity_id=user.id, request=request)
    db.commit()

    response.delete_cookie(settings.session_cookie_name, path="/")
    response.delete_cookie(settings.csrf_cookie_name, path="/")
    return Message(detail="Sesión cerrada.")


@router.get("/me", response_model=CurrentUserOut)
def me(db: DbSession, user: CurrentUser):
    return _user_out(db, user)


@router.post("/password", response_model=Message)
def change_password(
    payload: PasswordChange, request: Request, db: DbSession, user: CurrentUser
):
    if not verify_password(payload.current_password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La contraseña actual no es correcta.",
        )
    if payload.new_password == payload.current_password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="La nueva contraseña debe ser distinta de la actual.",
        )

    user.password_hash = hash_password(payload.new_password)
    user.must_change_password = False
    audit.record(db, action="auth.password_changed", actor=user,
                 entity_type="user", entity_id=user.id, request=request)
    db.commit()
    return Message(detail="Contraseña actualizada.")
