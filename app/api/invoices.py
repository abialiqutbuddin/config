from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db

router = APIRouter(prefix="/invoices", tags=["Invoices"])

@router.get("")
async def list_invoices(db: AsyncSession = Depends(get_db)):
    """TODO: Implement invoice listing"""
    return {"status": "TODO"}