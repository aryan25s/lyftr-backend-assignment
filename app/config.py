import os
from functools import lru_cache
from typing import Literal

from pydantic import BaseSettings, Field, validator


class Settings(BaseSettings):
    """Application configuration via environment variables."""

    app_name: str = Field("Webhook Service", env="APP_NAME")

    # Core config
    webhook_secret: str = Field("", env="WEBHOOK_SECRET")
    database_url: str = Field("/data/app.db", env="DATABASE_URL")
    log_level: str = Field("INFO", env="LOG_LEVEL")

    # Metrics toggle
    enable_metrics: bool = Field(True, env="ENABLE_METRICS")

    class Config:
        case_sensitive = False
        env_file = ".env"
        env_file_encoding = "utf-8"

    @validator("log_level")
    def normalize_log_level(cls, v: str) -> str:
        return v.upper()


@lru_cache()
def get_settings() -> Settings:
    """Return cached settings instance."""

    return Settings()


