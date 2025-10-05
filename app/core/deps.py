# app/core/deps.py
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.core.settings import settings
from app.payments.stripe_provider import StripePaymentProvider

engine = create_async_engine(settings.DATABASE_URL, pool_pre_ping=True)

# Explicit generic helps Pylance infer the yielded type
SessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine, expire_on_commit=False
)

async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Yield a single AsyncSession per request."""
    async with SessionLocal() as session:
        yield session

def get_stripe_provider() -> StripePaymentProvider:
    return StripePaymentProvider(
        api_key=settings.STRIPE_SECRET_KEY,
        webhook_secret=settings.STRIPE_WEBHOOK_SECRET,
    )