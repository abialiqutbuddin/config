from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db

router = APIRouter(prefix="/subscriptions", tags=["Subscriptions"])

@router.post("")
async def create_subscription(db: AsyncSession = Depends(get_db)):
    """TODO: Implement subscription creation"""
    return {"status": "TODO"}

@router.get("/{id}")
async def get_subscription(id: str, db: AsyncSession = Depends(get_db)):
    """TODO: Implement get subscription by id"""
    return {"status": "TODO"}