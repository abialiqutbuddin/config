# app/core/settings.py
from __future__ import annotations
from typing import Literal, Optional
from pydantic_settings import BaseSettings
from pydantic import field_validator, ConfigDict


class Settings(BaseSettings):
    # --- App ---
    APP_NAME: str = "Subscription Service"
    ENV: Literal["development", "staging", "production"] = "development"
    LOG_LEVEL: str = "INFO"

    # --- HTTP / CORS ---
    CORS_ORIGINS: str = "*"  # comma-separated list or "*"
    CORS_ALLOW_CREDENTIALS: bool = True
    CORS_ALLOW_METHODS: str = "*"   # comma-separated or "*"
    CORS_ALLOW_HEADERS: str = "*"   # comma-separated or "*"

    # --- Database ---
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/subscriptions"
    DB_ECHO: bool = False
    DB_POOL_SIZE: int = 5
    DB_MAX_OVERFLOW: int = 10
    DB_POOL_TIMEOUT: int = 30

    # --- Auth ---
    JWT_SECRET: str = "supersecretjwt"
    JWT_ALGORITHM: str = "HS256"
    JWT_ISSUER: Optional[str] = None
    JWT_AUDIENCE: Optional[str] = None
    DEV_JWT_SUBJECT: str = "dev-user"

    # --- Payments ---
    PAYMENTS_BACKEND: Literal["fake", "stripe"] = "fake"
    STRIPE_SECRET_KEY: str = ""         # required if PAYMENTS_BACKEND=stripe
    STRIPE_WEBHOOK_SECRET: str = ""     # required if PAYMENTS_BACKEND=stripe

    model_config = ConfigDict(env_file=".env", case_sensitive=False)

    @property
    def DEV_MODE(self) -> bool:
        return self.ENV == "development"

    @field_validator("STRIPE_SECRET_KEY", "STRIPE_WEBHOOK_SECRET")
    @classmethod
    def _require_stripe_if_real(cls, v: str, info):
        # When PAYMENTS_BACKEND=stripe, both keys must be non-empty
        # (We can’t see PAYMENTS_BACKEND here directly; validate in post init as well.)
        return v

    @field_validator("CORS_ORIGINS", "CORS_ALLOW_METHODS", "CORS_ALLOW_HEADERS")
    @classmethod
    def _norm_csv(cls, v: str) -> str:
        return ",".join([piece.strip() for piece in v.split(",")]) if v else v

    def validate_payments(self) -> None:
        if self.PAYMENTS_BACKEND == "stripe":
            if not self.STRIPE_SECRET_KEY or not self.STRIPE_WEBHOOK_SECRET:
                raise ValueError("STRIPE_SECRET_KEY and STRIPE_WEBHOOK_SECRET are required when PAYMENTS_BACKEND=stripe")


settings = Settings()
# Post init checks that are cross-field aware
settings.validate_payments()