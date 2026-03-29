from pathlib import Path
from typing import List, Optional, Union

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# app/core/config.py -> parents[2] = repository root (stable regardless of process cwd)
_PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=str(_PROJECT_ROOT / ".env"),
        case_sensitive=True,
    )

    # Database
    DATABASE_URL: str = "sqlite:///./apex_advisor.db"
    TEST_DATABASE_URL: Optional[str] = None

    # JWT
    SECRET_KEY: str = "dev-secret-key-change-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    # LLM APIs
    OPENAI_API_KEY: Optional[str] = None
    OPENAI_MODEL: str = "gpt-4"
    CLAUDE_API_KEY: Optional[str] = None
    # Kimi (Moonshot) — optional; same names as in .env so startup does not fail on extra keys
    Kimi_API_KEY: Optional[str] = None
    Kimi_MODEL: str = "gpt-4"

    # Application
    DEBUG: bool = False
    API_V1_STR: str = "/api/v1"
    PROJECT_NAME: str = "Apex Advisor"

    # CORS
    CORS_ORIGINS: List[str] = []

    # Logging
    LOG_LEVEL: str = "INFO"

    @field_validator("CORS_ORIGINS", mode="before")
    @classmethod
    def assemble_cors_origins(cls, v: Union[str, List[str]]) -> List[str]:
        if isinstance(v, str) and not v.startswith("["):
            return [i.strip() for i in v.split(",") if i.strip()]
        if isinstance(v, list):
            return v
        raise ValueError(v)

    @model_validator(mode="after")
    def resolve_sqlite_url_to_project_root(self) -> "Settings":
        """sqlite:///./file.db is cwd-relative by default; anchor to repo root for uvicorn/alembic parity."""
        url = self.DATABASE_URL
        if not url.startswith("sqlite"):
            return self
        if url.startswith("sqlite:///:memory:"):
            return self
        prefix = "sqlite:///./"
        if url.startswith(prefix):
            rel = url[len(prefix) :]
            abs_path = (_PROJECT_ROOT / rel).resolve()
            object.__setattr__(self, "DATABASE_URL", f"sqlite:///{abs_path.as_posix()}")
        return self


settings = Settings()
