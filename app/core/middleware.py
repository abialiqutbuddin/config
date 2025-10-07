# app/core/middleware.py
from __future__ import annotations

import hashlib
import json
from typing import Optional
from starlette.types import ASGIApp, Receive, Scope, Send, Message
from starlette.responses import Response
from sqlalchemy import text

from app.core.deps import SessionLocal

IDEMPOTENCY_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
    "/stripe/webhook",
    "/favicon.ico",
)

class IdempotencyMiddleware:
    """
    Lean idempotency storage (Option A) that:
      - Applies to POST/PUT/PATCH/DELETE.
      - Exempts OPTIONS and well-known non-mutating paths.
      - Requires Idempotency-Key + X-Project-Id (422 if missing).
      - Computes request body sha256 -> request_hash.
      - If key exists:
          * if request_hash differs => 409 Conflict (prevents accidental reuse)
          * else return stored response (status + JSON body).
      - Otherwise, calls downstream; if response status < 500, stores {response JSON, status, request_hash}.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = (scope.get("method") or "GET").upper()
        path = scope.get("path") or "/"

        # Exempt preflight and specific routes
        if method == "OPTIONS" or any(path.startswith(p) for p in IDEMPOTENCY_EXEMPT_PREFIXES):
            return await self.app(scope, receive, send)

        # Only guard mutating methods
        if method not in ("POST", "PUT", "PATCH", "DELETE"):
            return await self.app(scope, receive, send)

        # Pull headers we need
        idem_key = _header(scope, b"idempotency-key")
        project_id = _header(scope, b"x-project-id")

        if not idem_key or not project_id:
            return await _json(
                send,
                422,
                {"error": "Missing Idempotency-Key or X-Project-Id header"},
            )

        # Read entire request body once
        full_body = await _read_body(receive)
        req_hash = hashlib.sha256(full_body).hexdigest()

        # Check existing key
        async with SessionLocal() as session:
            res = await session.execute(
                text(
                    """
                    SELECT status, response, request_hash
                    FROM idempotency_keys
                    WHERE project_id=:p AND key=:k
                    """
                ),
                {"p": project_id, "k": idem_key},
            )
            row = res.first()
            if row:
                status_str, stored_response, stored_hash = row
                # If same key but different body => 409 Conflict
                if stored_hash and stored_hash != req_hash:
                    return await _json(
                        send,
                        409,
                        {"error": "Idempotency key reused with a different request body"},
                    )

                # Return stored response
                status_code = int(status_str) if status_str and status_str.isdigit() else 200
                body_bytes = json.dumps(stored_response).encode("utf-8")
                await _write_min_json(send, status_code, body_bytes)
                return

        # No stored response → forward request
        # Re-inject body to downstream
        async def receive_wrapper() -> Message:
            nonlocal full_body
            body = full_body
            full_body = b""
            return {"type": "http.request", "body": body, "more_body": False}

        # Capture downstream response
        captured_status = 200
        captured_body = b""

        async def send_wrapper(message: Message):
            nonlocal captured_status, captured_body
            if message["type"] == "http.response.start":
                captured_status = int(message.get("status") or 200)
                await send(message)
            elif message["type"] == "http.response.body":
                captured_body += message.get("body", b"")
                await send(message)
            else:
                await send(message)

        await self.app(scope, receive_wrapper, send_wrapper)

        # Persist (best-effort) if < 500 and JSON-ish
        if captured_status < 500:
            try:
                payload_to_store: Optional[object]
                try:
                    payload_to_store = json.loads(captured_body.decode() or "null")
                except Exception:
                    # Not JSON — store as string inside JSON container
                    payload_to_store = {"_non_json": captured_body.decode(errors="ignore")}

                async with SessionLocal() as session:
                    await session.execute(
                        text(
                            """
                            INSERT INTO idempotency_keys (project_id, key, request_hash, response, status)
                            VALUES (:p, :k, :h, :r, :s)
                            ON CONFLICT (project_id, key) DO NOTHING
                            """
                        ),
                        {
                            "p": project_id,
                            "k": idem_key,
                            "h": req_hash,
                            "r": payload_to_store,
                            "s": str(captured_status),
                        },
                    )
                    await session.commit()
            except Exception:
                # best-effort: never block response path
                pass


# ---------- helpers ----------

async def _read_body(receive: Receive) -> bytes:
    chunks: list[bytes] = []
    more = True
    while more:
        msg = await receive()
        if msg["type"] != "http.request":
            break
        chunks.append(msg.get("body", b""))
        more = msg.get("more_body", False)
    return b"".join(chunks)

def _header(scope: Scope, name: bytes) -> Optional[str]:
    headers = dict((k.lower(), v) for k, v in (scope.get("headers") or []))
    v = headers.get(name)
    return v.decode() if v else None

async def _json(send: Send, status_code: int, data: dict):
    body = json.dumps(data).encode("utf-8")
    await _write_min_json(send, status_code, body)

async def _write_min_json(send: Send, status_code: int, body: bytes):
    await send(
        {
            "type": "http.response.start",
            "status": status_code,
            "headers": [
                (b"content-type", b"application/json"),
            ],
        }
    )
    await send(
        {
            "type": "http.response.body",
            "body": body,
            "more_body": False,
        }
    )