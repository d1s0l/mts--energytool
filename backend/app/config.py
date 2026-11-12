"""Настройки из переменных окружения и локального .env."""

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql://energytool:energytool@localhost:5432/energytool"
    knowledge_base_path: Path = Path("../data/knowledge-base.zip")
    max_workers: int = Field(default=3, ge=1, le=8)
    api_access_token: str = Field(default="", repr=False)
