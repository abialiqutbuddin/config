from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db

router = APIRouter(prefix="/config", tags=["Config"])

@router.post("")
async def publish_config(db: AsyncSession = Depends(get_db)):
    """TODO: Implement config publishing endpoint"""
    return {"status": "TODO"}