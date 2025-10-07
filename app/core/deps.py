# app/core/deps.py
from functools import lru_cache
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.settings import settings
from app.payments.stripe_provider import StripePaymentProvider
from app.payments.fake_provider import FakeStripeProvider

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with SessionLocal() as session:
        yield session

@lru_cache(maxsize=1)
def _payments_singleton():
    if settings.PAYMENTS_BACKEND == "fake" or not settings.STRIPE_SECRET_KEY:
        return FakeStripeProvider()
    return StripePaymentProvider(
        api_key=settings.STRIPE_SECRET_KEY,
        webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
    )

def get_stripe_provider():
    # FastAPI will call this each request, but we return the cached singleton
    return _payments_singleton()