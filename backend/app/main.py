"""SCAE API application."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import admin, auth, catalog, dashboard, events, incidents, registry
from app.core.config import get_settings

settings = get_settings()
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s · %(message)s",
)
logger = logging.getLogger("scae")

@asynccontextmanager
async def lifespan(_app: FastAPI):
    logger.info(
        "SCAE API iniciada · entorno=%s · reconocimiento=%s",
        settings.environment, settings.recognition_backend,
    )
    yield


app = FastAPI(
    lifespan=lifespan,
    title=f"{settings.app_name} — {settings.app_title}",
    version="2.0.0",
    description=(
        "API de la plataforma de control de acceso vehicular. "
        "Los datos marcados como `demo` no corresponden a registros reales."
    ),
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
)

# The SPA runs on a different origin in development. Credentials are allowed
# because the session travels in a cookie; the origin list is explicit and
# never a wildcard, which would be incompatible with credentialed requests.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-CSRF-Token"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


@app.exception_handler(RequestValidationError)
async def validation_handler(request: Request, exc: RequestValidationError):
    """Return a readable message instead of Pydantic's nested error tree."""
    first = exc.errors()[0] if exc.errors() else {}
    field = ".".join(str(p) for p in first.get("loc", [])[1:]) or "solicitud"
    return JSONResponse(
        status_code=422,
        content={"detail": f"{field}: {first.get('msg', 'valor inválido')}"},
    )


API = "/api/v1"
app.include_router(auth.router, prefix=API)
app.include_router(catalog.router, prefix=API)
app.include_router(registry.router, prefix=API)
app.include_router(events.router, prefix=API)
app.include_router(incidents.router, prefix=API)
app.include_router(dashboard.router, prefix=API)
app.include_router(admin.router, prefix=API)


@app.get("/api/health", tags=["meta"])
def health():
    """Unauthenticated liveness probe."""
    return {"status": "ok", "service": settings.app_name, "version": "2.0.0"}
