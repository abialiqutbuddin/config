# app/api/config.py
from __future__ import annotations
from uuid import UUID
import hashlib, json

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import get_db
from app.persistence.repo import ConfigRepo
from app.schemas.validator import validate_config_or_400

router = APIRouter(prefix="/config", tags=["Config"])

def _etag_of_json(j: dict) -> str:
    # stable ETag of the inner JSON document
    payload = json.dumps(j, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

@router.post("", status_code=status.HTTP_201_CREATED)
async def publish_config(
    request: Request,
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    """
    Accepts a *wrapped* payload:
      {
        "versionLabel": "<string>",
        "json": { ... actual billing config ... }
      }
    """
    wrapper = await request.json()
    version_label = wrapper.get("versionLabel")
    cfg = wrapper.get("json")

    if not isinstance(version_label, str) or not version_label.strip():
        raise HTTPException(status_code=422, detail={"type": "validation_error", "message": "versionLabel is required"})
    if not isinstance(cfg, dict):
        raise HTTPException(status_code=422, detail={"type": "validation_error", "message": "json (config object) is required"})

    # validate inner json
    validate_config_or_400(cfg)

    repo = ConfigRepo(db)

    # enforce uniqueness per (project_id, version_label)
    existing = await repo.get_by_label(project_id, version_label)
    if existing:
        raise HTTPException(
            status_code=409,
            detail={"type": "conflict", "message": f"versionLabel '{version_label}' already exists for this project"},
        )

    row = await repo.create(project_id=project_id, version_label=version_label, json_data=cfg)
    await db.commit()

    etag = _etag_of_json(cfg)
    return Response(
        content=json.dumps({
            "id": str(row.id),
            "projectId": row.project_id,
            "versionLabel": row.version_label,
            "json": row.json,
        }),
        media_type="application/json",
        headers={"ETag": etag},
        status_code=status.HTTP_201_CREATED,
    )

@router.get("/latest")
async def get_latest_config(
    db: AsyncSession = Depends(get_db),
    project_id: str = Header(..., alias="X-Project-Id"),
):
    repo = ConfigRepo(db)
    row = await repo.get_latest(project_id)
    if not row:
        raise HTTPException(status_code=404, detail={"type": "not_found", "message": "No config for project"})

    etag = _etag_of_json(row.json or {})
    return Response(
        content=json.dumps({
            "id": str(row.id),
            "projectId": row.project_id,
            "versionLabel": row.version_label,
            "json": row.json,
        }),
        media_type="application/json",
        headers={"ETag": etag},
    )

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

    etag = _etag_of_json(row.json or {})
    return Response(
        content=json.dumps({
            "id": str(row.id),
            "projectId": row.project_id,
            "versionLabel": row.version_label,
            "json": row.json,
        }),
        media_type="application/json",
        headers={"ETag": etag},
    )