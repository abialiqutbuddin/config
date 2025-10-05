from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    APP_NAME: str = "Subscription Service"
    ENV: str = "development"
    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/subscriptions"
    STRIPE_SECRET_KEY: str = "sk_test_..."
    STRIPE_WEBHOOK_SECRET: str = "whsec_..."
    JWT_SECRET: str = "supersecretjwt"
    JWT_ALGORITHM: str = "HS256"
    LOG_LEVEL: str = "INFO"

    class Config:
        env_file = ".env"


settings = Settings()