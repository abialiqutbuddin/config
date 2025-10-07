# app/core/deps.py
from __future__ import annotations
from functools import lru_cache
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.settings import settings
from app.payments.stripe_provider import StripePaymentProvider
from app.payments.fake_provider import FakeStripeProvider

# Engine & session factory
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=settings.DB_POOL_SIZE,
    max_overflow=settings.DB_MAX_OVERFLOW,
    pool_timeout=settings.DB_POOL_TIMEOUT,
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

@lru_cache(maxsize=1)
def get_stripe_provider_cached():
    if settings.PAYMENTS_BACKEND == "fake" or not settings.STRIPE_SECRET_KEY:
        return FakeStripeProvider()
    return StripePaymentProvider(
        api_key=settings.STRIPE_SECRET_KEY,
        webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
    )

# FastAPI Depends-friendly factory
def get_stripe_provider():
    return get_stripe_provider_cached()