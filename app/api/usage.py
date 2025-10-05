from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db

router = APIRouter(prefix="/usage", tags=["Usage"])

@router.post("")
async def record_usage(db: AsyncSession = Depends(get_db)):
    """TODO: Implement usage posting"""
    return {"status": "TODO"}