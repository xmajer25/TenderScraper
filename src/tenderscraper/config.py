from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables and .env."""
    poptavej_username: str | None = Field(default=None, validation_alias="POPTAVEJ_USERNAME")
    poptavej_password: str | None = Field(default=None, validation_alias="POPTAVEJ_PASSWORD")

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    data_dir: Path = Field(default=Path("./data"))
    tenders_dir: Path = Field(default=Path("./data/tenders"))
    sqlite_path: Path = Field(default=Path("./data/index.sqlite"))

    http_timeout_s: int = 30
    http_user_agent: str = "MetaIT-TenderScraper/0.1.0"
    max_concurrent_downloads: int = 8

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.tenders_dir.mkdir(parents=True, exist_ok=True)
        self.sqlite_path.parent.mkdir(parents=True, exist_ok=True)


settings = Settings()
