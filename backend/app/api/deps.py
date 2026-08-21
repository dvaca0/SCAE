"""Request-scoped dependencies: session, current user, role gates, CSRF."""
from __future__ import annotations

from typing import Annotated, Callable, Optional

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.core.db import get_db
from app.core.security import csrf_tokens_match, decode_access_token
from app.models import Role, User

settings = get_settings()

DbSession = Annotated[Session, Depends(get_db)]

# Methods that change state must present a CSRF token.
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def get_current_user(request: Request, db: DbSession) -> User:
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="No hay sesión activa.",
        )
    payload = decode_access_token(token)
    if not payload:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La sesión expiró o no es válida.",
        )

    user = db.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="La cuenta ya no está activa.",
        )

    # The cookie is sent automatically by the browser, so state-changing
    # requests additionally prove they came from our own JS.
    if request.method in UNSAFE_METHODS:
        cookie_csrf = request.cookies.get(settings.csrf_cookie_name)
        header_csrf = request.headers.get("X-CSRF-Token")
        if not csrf_tokens_match(cookie_csrf, header_csrf):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Token CSRF ausente o inválido.",
            )

    request.state.user = user
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: Role) -> Callable[..., User]:
    """Route guard. Admin is never implicitly granted — it is listed where
    it applies, so reading a route tells you exactly who can call it."""

    allowed = set(roles)

    def _guard(user: CurrentUser) -> User:
        if user.role not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Tu rol no tiene permiso para esta operación.",
            )
        return user

    return _guard


# Common combinations, named for readability at the call site.
AdminOnly = Annotated[User, Depends(require_roles(Role.ADMIN))]
AdminOrSupervisor = Annotated[User, Depends(require_roles(Role.ADMIN, Role.SUPERVISOR))]
Operators = Annotated[User, Depends(require_roles(Role.ADMIN, Role.SUPERVISOR, Role.GUARD))]
AnyRole = Annotated[
    User, Depends(require_roles(Role.ADMIN, Role.SUPERVISOR, Role.GUARD, Role.AUDITOR))
]


def client_ip(request: Request) -> Optional[str]:
    if request.client:
        return request.client.host
    return None
