"""Application configuration, loaded from environment / .env."""
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[2]
REPO_ROOT = BACKEND_ROOT.parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(REPO_ROOT / ".env", BACKEND_ROOT / ".env"),
        env_prefix="SCAE_",
        extra="ignore",
    )

    # --- Identity -------------------------------------------------------
    app_name: str = "SCAE"
    app_title: str = "Sistema de Control de Acceso Vehicular"
    environment: str = "development"

    # --- Security -------------------------------------------------------
    # No default secret: the app refuses to boot without one outside dev.
    secret_key: str = ""
    access_token_ttl_minutes: int = 60 * 8
    session_cookie_name: str = "scae_session"
    csrf_cookie_name: str = "scae_csrf"
    cookie_secure: bool = False  # True behind HTTPS in production

    # --- Persistence ----------------------------------------------------
    database_url: str = f"sqlite:///{BACKEND_ROOT / 'data' / 'scae.db'}"

    # --- Web ------------------------------------------------------------
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ]

    # --- Recognition ----------------------------------------------------
    # Backend selection is explicit so a machine without the CV stack can
    # still run the platform against the stub recognizer.
    recognition_backend: str = "yolo_easyocr"  # yolo_easyocr | stub
    detector_model: str = "yolov5l.pt"
    ocr_languages: list[str] = ["en"]
    ocr_use_gpu: bool = False
    # Below this confidence the decision engine refuses to auto-decide.
    review_confidence_threshold: float = 0.55
    # Evidence images captured at the lane.
    evidence_dir: Path = BACKEND_ROOT / "data" / "evidence"
    max_upload_bytes: int = 12 * 1024 * 1024

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    if not settings.secret_key:
        if settings.is_production:
            raise RuntimeError(
                "SCAE_SECRET_KEY must be set in production. Refusing to start "
                "with an ephemeral signing key."
            )
        # Development fallback: stable per-checkout, never shipped to prod.
        import hashlib

        settings.secret_key = hashlib.sha256(
            f"scae-dev-{BACKEND_ROOT}".encode()
        ).hexdigest()
    settings.evidence_dir.mkdir(parents=True, exist_ok=True)
    if settings.database_url.startswith("sqlite:///"):
        Path(settings.database_url.removeprefix("sqlite:///")).parent.mkdir(
            parents=True, exist_ok=True
        )
    return settings
