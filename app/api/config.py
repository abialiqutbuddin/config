from uuid import UUID
from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession
from app.core.deps import get_db
from app.persistence.repo import ConfigRepo
from app.schemas.validator import validate_config_or_400

router = APIRouter(prefix="/config", tags=["Config"])

@router.post("", status_code=201)
async def publish_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Publish a new config version for the project in X-Project-Id.
    Body is the full config JSON (not wrapped).
    """
    body = await request.json()
    validate_config_or_400(body)

    repo = ConfigRepo(db)
    row = await repo.create(project_id=project_id, version_label=body["label"], json_data=body)
    await db.commit()
    return {
        "id": str(row.id),
        "projectId": row.project_id,
        "versionLabel": row.version_label,
        "createdAt": row.created_at.isoformat() if row.created_at else None,
    }

@router.get("/latest")
async def get_latest_config(
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = ConfigRepo(db)
    row = await repo.get_latest(project_id)
    if not row:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "No config for project"})
    return {"id": str(row.id), "label": row.version_label, "config": row.json}

@router.get("/{version_id}")
async def get_config_version(
    version_id: UUID,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = ConfigRepo(db)
    row = await repo.get_by_id(version_id)
    if not row or row.project_id != project_id:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "Config version not found"})
    return {"id": str(row.id), "label": row.version_label, "config": row.json}