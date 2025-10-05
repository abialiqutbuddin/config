import hashlib
import json
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import text
from app.core.deps import get_db
from contextlib import asynccontextmanager


@asynccontextmanager
async def get_db_session():
    async for session in get_db():
        yield session


class IdempotencyMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.method not in ("POST", "PUT", "PATCH"):
            return await call_next(request)

        idem_key = request.headers.get("Idempotency-Key")
        project_id = request.headers.get("X-Project-Id")

        if not idem_key or not project_id:
            return Response(
                content=json.dumps({"error": "Missing Idempotency-Key or X-Project-Id header"}),
                status_code=422,
                media_type="application/json",
            )

        body = await request.body()
        request_hash = hashlib.sha256(body).hexdigest()

        async with get_db_session() as session:
            res = await session.execute(
                text("SELECT response FROM idempotency_keys WHERE project_id=:p AND key=:k"),
                {"p": project_id, "k": idem_key},
            )
            row = res.first()
            if row and row[0]:
                return Response(content=json.dumps(row[0]), media_type="application/json")

        response = await call_next(request)

        try:
            if response.status_code < 500:
                data = await response.body()
                async with get_db_session() as session:
                    await session.execute(
                        text(
                            "INSERT INTO idempotency_keys(project_id, key, request_hash, response, status)"
                            " VALUES(:p, :k, :h, :r, 'stored') ON CONFLICT DO NOTHING"
                        ),
                        {"p": project_id, "k": idem_key, "h": request_hash, "r": json.loads(data.decode())},
                    )
                    await session.commit()
        except Exception:
            pass

        return response