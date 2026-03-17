from __future__ import annotations

import tempfile
from pathlib import Path
from urllib.parse import quote

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""
    poptavej_username: str | None = Field(default=None, validation_alias="POPTAVEJ_USERNAME")
    poptavej_password: str | None = Field(default=None, validation_alias="POPTAVEJ_PASSWORD")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    database_url: str | None = Field(default=None, validation_alias="DATABASE_URL")
    scratch_dir: Path = Field(
        default=Path(tempfile.gettempdir()) / "tenderscraper",
        validation_alias="SCRATCH_DIR",
    )
    render: str | None = Field(default=None, validation_alias="RENDER")
    render_service_name: str | None = Field(default=None, validation_alias="RENDER_SERVICE_NAME")

    http_timeout_s: int = 30
    http_user_agent: str = "MetaIT-TenderScraper/0.1.0"
    max_concurrent_downloads: int = 8
    storage_backend: str = Field(default="s3", validation_alias="STORAGE_BACKEND")
    s3_bucket: str | None = Field(default=None, validation_alias="S3_BUCKET")
    s3_region: str | None = Field(default=None, validation_alias="S3_REGION")
    s3_endpoint_url: str | None = Field(default=None, validation_alias="S3_ENDPOINT_URL")
    s3_access_key_id: str | None = Field(default=None, validation_alias="S3_ACCESS_KEY_ID")
    s3_secret_access_key: str | None = Field(default=None, validation_alias="S3_SECRET_ACCESS_KEY")
    s3_public_base_url: str | None = Field(default=None, validation_alias="S3_PUBLIC_BASE_URL")
    s3_presign_expiry_s: int = Field(default=3600, validation_alias="S3_PRESIGN_EXPIRY_S")

    def ensure_dirs(self) -> None:
        self.scratch_dir.mkdir(parents=True, exist_ok=True)
        (self.scratch_dir / "auth").mkdir(parents=True, exist_ok=True)

    @property
    def default_poptavej_state_path(self) -> Path:
        return self.scratch_dir / "auth" / "poptavej_state.json"

    @property
    def running_on_render(self) -> bool:
        return bool((self.render or "").strip() or (self.render_service_name or "").strip())

    @property
    def normalized_database_url(self) -> str:
        url = (self.database_url or "").strip()
        if not url:
            raise ValueError(
                "DATABASE_URL is required. Use the local Docker Postgres URL in .env for local runs, "
                "or wire DATABASE_URL from the Render Postgres instance when deploying to Render."
            )
        if self.running_on_render and "@postgres:" in url:
            raise ValueError(
                "DATABASE_URL points to the Docker Compose host 'postgres', which does not exist on Render. "
                "Set DATABASE_URL from the Render Postgres instance."
            )
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://") and not url.startswith("postgresql+psycopg://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def uses_s3_storage(self) -> bool:
        return self.storage_backend.strip().lower() == "s3"

    def require_s3_settings(self) -> None:
        required = {
            "S3_BUCKET": self.s3_bucket,
            "S3_ACCESS_KEY_ID": self.s3_access_key_id,
            "S3_SECRET_ACCESS_KEY": self.s3_secret_access_key,
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ValueError(f"Missing required S3 settings: {', '.join(missing)}")

    def public_object_url(self, storage_key: str) -> str | None:
        base = (self.s3_public_base_url or "").rstrip("/")
        if not base:
            return None
        return f"{base}/{quote(storage_key, safe='/')}"


settings = Settings()
