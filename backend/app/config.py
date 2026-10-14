from functools import lru_cache
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/app"
    jwt_secret: SecretStr = Field(min_length=32)
    environment: Literal["development", "test", "production"] = "development"
    frontend_url: str = "http://localhost:3000"
    cors_origins: list[str] = ["http://localhost:3000"]
    cookie_secure: bool = False
    access_token_minutes: int = Field(default=10, ge=1, le=30)
    refresh_token_days: int = Field(default=14, ge=1, le=90)
    openai_api_key: SecretStr | None = None
    openai_model: str = "gpt-4o-mini"
    agent_max_steps: int = Field(default=6, ge=1, le=12)
    agent_timeout_seconds: float = Field(default=45, gt=0, le=120)
    agent_concurrency: int = Field(default=8, ge=1, le=64)
    agent_queue_timeout_seconds: float = Field(default=2, gt=0, le=10)
    oidc_issuer: str | None = None
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_redirect_uri: str = "http://localhost:3000/api/v1/auth/oidc/callback"

    @field_validator(
        "openai_api_key", "oidc_issuer", "oidc_client_id", "oidc_client_secret", mode="before"
    )
    @classmethod
    def empty_is_none(cls, value: object) -> object:
        return None if value == "" else value

    @model_validator(mode="after")
    def validate_security(self) -> "Settings":
        if "*" in self.cors_origins:
            raise ValueError("Credentialed CORS must use explicit origins")
        for origin in [self.frontend_url, *self.cors_origins]:
            parsed = urlparse(origin)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                raise ValueError("Frontend and CORS origins must be absolute HTTP(S) URLs")
            if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
                raise ValueError("Frontend and CORS origins cannot contain paths/query/fragment")
        if self.frontend_url.rstrip("/") not in self.cors_origins:
            raise ValueError("FRONTEND_URL must be included in CORS_ORIGINS")
        oidc = [self.oidc_issuer, self.oidc_client_id, self.oidc_client_secret]
        if any(oidc) and not all(oidc):
            raise ValueError("Configure all three OIDC settings or none")
        if self.oidc_issuer and urlparse(self.oidc_issuer).scheme != "https":
            raise ValueError("OIDC issuer must use HTTPS")
        if self.environment == "production":
            if not self.cookie_secure:
                raise ValueError("Production requires COOKIE_SECURE=true")
            if any(
                urlparse(url).scheme != "https"
                for url in [self.frontend_url, *self.cors_origins, self.oidc_redirect_uri]
            ):
                raise ValueError("Production browser URLs must use HTTPS")
        return self

    @property
    def oidc_enabled(self) -> bool:
        return bool(self.oidc_issuer and self.oidc_client_id and self.oidc_client_secret)


@lru_cache
def get_settings() -> Settings:
    return Settings()
