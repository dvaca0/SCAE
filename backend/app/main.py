"""SCAE API application."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import (
    admin, auth, camera, catalog, dashboard, events, incidents, registry,
)
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

    # --- PARCHE LOCAL (Raspberry Pi 5): vigilancia permanente del carril ---
    # La caseta tiene que estar mirando el carril siempre, no solo cuando un
    # guardia abre el navegador y le da a un boton. Con SCAE_LPR_AUTOSTART=true
    # el vigilante arranca con el servicio y, como systemd lo reinicia solo
    # (Restart=always), sobrevive a un fallo. El vigilante cuenta como
    # espectador de la captura compartida, asi que mantiene vivo el flujo RTSPS
    # sin abrir una segunda conexion a la camara.
    watcher = None
    try:
        from app.recognition.lane_watcher import get_watcher

        watcher = get_watcher()
        if watcher._cfg()["autostart"]:
            if watcher.start():
                logger.info("Vigilancia de carril iniciada automaticamente (24/7)")
        else:
            logger.info("Vigilancia de carril en manual (SCAE_LPR_AUTOSTART=false)")
    except Exception:
        # Que el reconocimiento no arranque no puede tumbar la API: el guardia
        # todavia tiene captura manual y resolucion de eventos.
        logger.exception("No se pudo iniciar la vigilancia automatica del carril")

    try:
        yield
    finally:
        # Apagado ordenado: soltar la camara y dejar el servo sin pulsos.
        if watcher is not None:
            try:
                watcher.stop()
            except Exception:
                logger.debug("Fallo deteniendo el vigilante", exc_info=True)
        try:
            from app.hardware.servo_gate import _gates

            for gate in list(_gates.values()):
                gate.shutdown()
        except Exception:
            logger.debug("Fallo liberando el servo", exc_info=True)


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
# PARCHE LOCAL (Raspberry Pi 5) - vista en vivo de la camara.
# Ver /opt/scae/PATCHES.md y app/hardware/camera_stream.py
app.include_router(camera.router, prefix=API)


@app.get("/api/health", tags=["meta"])
def health():
    """Unauthenticated liveness probe."""
    return {"status": "ok", "service": settings.app_name, "version": "2.0.0"}


# ---------------------------------------------------------------------------
# PARCHE LOCAL (Raspberry Pi 5) — ver /opt/scae/PATCHES.md
#
# frontend/src/lib/api.ts llama a /api/v1 con credentials: 'same-origin', y la
# sesion viaja en una cookie httpOnly con CSRF. Servir el SPA en otro puerto
# romperia el login. En vez de anadir un proxy inverso (nginx querria el puerto
# 80, que en este host lo ocupa energy-node-portal, de la tesis), este mismo
# uvicorn sirve los estaticos compilados. Un servicio, un puerto, mismo origen.
#
# Se registra DESPUES de todos los routers, de modo que las rutas de la API
# siempre ganan: FastAPI resuelve en orden de registro.
# ---------------------------------------------------------------------------
from pathlib import Path as _Path  # noqa: E402

from fastapi import HTTPException  # noqa: E402
from fastapi.responses import FileResponse  # noqa: E402
from fastapi.staticfiles import StaticFiles  # noqa: E402

_DIST = _Path(__file__).resolve().parents[2] / "frontend" / "dist"

if _DIST.is_dir():
    _ASSETS = _DIST / "assets"

    # PARCHE LOCAL (Raspberry Pi 5) - cabeceras de cache del SPA.
    # Sin esto, index.html salia con ETag y Last-Modified pero SIN
    # Cache-Control, y los navegadores aplican cache heuristica: reutilizan su
    # copia sin revalidar durante una fraccion del tiempo transcurrido desde
    # Last-Modified. Resultado: tras desplegar una version nueva, el navegador
    # seguia cargando el index.html viejo, que apunta al bundle viejo, y las
    # funciones nuevas no aparecian. Paso de verdad con el panel de camara.
    #
    # index.html: no-cache (revalidar siempre; es minusculo).
    # /assets/*: el nombre lleva hash del contenido, asi que es inmutable y se
    # puede cachear un ano. Cambiar el contenido cambia el nombre.
    _HTML_CACHE = {"Cache-Control": "no-cache, must-revalidate"}
    _ASSET_CACHE = "public, max-age=31536000, immutable"

    class _ImmutableStatic(StaticFiles):
        def file_response(self, *args, **kwargs):
            response = super().file_response(*args, **kwargs)
            response.headers["Cache-Control"] = _ASSET_CACHE
            return response

    if _ASSETS.is_dir():
        app.mount("/assets", _ImmutableStatic(directory=_ASSETS), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def serve_spa(full_path: str):
        """Sirve un fichero del build, o index.html para las rutas del SPA."""
        # Una ruta /api/* que llegue hasta aqui no existe: devolver JSON 404 en
        # lugar de index.html, para no confundir al cliente de la API.
        if full_path.startswith("api/"):
            raise HTTPException(status_code=404, detail="Ruta de API no encontrada")
        if full_path:
            candidate = (_DIST / full_path).resolve()
            # is_relative_to corta cualquier intento de path traversal.
            if candidate.is_file() and candidate.is_relative_to(_DIST):
                return FileResponse(candidate, headers=_HTML_CACHE)
        # react-router resuelve la ruta en el navegador.
        return FileResponse(_DIST / "index.html", headers=_HTML_CACHE)

    logger.info("SPA servido desde %s", _DIST)
else:
    logger.warning(
        "frontend/dist no existe: la API responde, pero no hay interfaz web. "
        "Compilar con: cd frontend && npm run build"
    )
