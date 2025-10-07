# app/core/middleware.py
from __future__ import annotations
import hashlib
import json
from typing import Callable, Awaitable
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
    ASGI middleware that:
      - Applies to POST/PUT/PATCH only.
      - Requires Idempotency-Key and X-Project-Id.
      - Returns stored response if the same key was used before.
      - Stores successful (<500) responses.
      - Exempts specific paths and all OPTIONS requests.
    """

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)

        method = scope.get("method", "GET").upper()
        path = scope.get("path") or "/"

        # Exempt preflight and exempt paths
        if method == "OPTIONS" or any(path.startswith(p) for p in IDEMPOTENCY_EXEMPT_PREFIXES):
            return await self.app(scope, receive, send)

        if method not in ("POST", "PUT", "PATCH"):
            return await self.app(scope, receive, send)

        # Read request body once; re-inject for downstream app
        body_chunks: list[bytes] = []
        async def _recv() -> Message:
            msg = await receive()
            if msg["type"] == "http.request":
                body_chunks.append(msg.get("body", b""))
                if not msg.get("more_body", False):
                    return msg
            return msg

        idem_key = self._header(scope, b"idempotency-key")
        project_id = self._header(scope, b"x-project-id")

        if not idem_key or not project_id:
            resp = Response(
                content=json.dumps({"error": "Missing Idempotency-Key or X-Project-Id header"}),
                media_type="application/json",
                status_code=422,
            )
            return await resp(scope, receive, send)

        # Peek body by consuming the whole stream once
        full_body = b""
        more = True
        while more:
            msg = await _recv()
            if msg["type"] == "http.request":
                full_body += body_chunks[-1] if body_chunks else b""
                more = msg.get("more_body", False)
            else:
                break

        req_hash = hashlib.sha256(full_body).hexdigest()

        # Try load stored response
        async with SessionLocal() as session:
            res = await session.execute(
                text("""
                    SELECT status, headers, body
                    FROM idempotency_keys
                    WHERE project_id=:p AND key=:k
                """),
                {"p": project_id, "k": idem_key},
            )
            row = res.first()
            if row:
                status, headers_json, body_json = row
                headers = json.loads(headers_json or "{}")
                body_bytes = (json.dumps(body_json) if isinstance(body_json, (dict, list)) else (body_json or "")).encode("utf-8")
                async def _send(replay_msg: Message):
                    if replay_msg["type"] == "http.response.start":
                        hdrs = [(k.encode(), v.encode()) for k, v in headers.items()]
                        replay_msg = {
                            "type": "http.response.start",
                            "status": status,
                            "headers": hdrs,
                        }
                    elif replay_msg["type"] == "http.response.body":
                        replay_msg = {
                            "type": "http.response.body",
                            "body": body_bytes,
                            "more_body": False,
                        }
                    await send(replay_msg)
                # Short-circuit with stored response
                await _send({"type": "http.response.start"})
                await _send({"type": "http.response.body"})
                return

        # No stored response → call app and capture outgoing response
        started = False
        resp_status = 200
        resp_headers: dict[str, str] = {}
        resp_body = b""

        async def send_wrapper(message: Message):
            nonlocal started, resp_status, resp_headers, resp_body
            if message["type"] == "http.response.start":
                started = True
                resp_status = message.get("status", 200)
                # headers: list[tuple[bytes, bytes]]
                hdrs_list = message.get("headers") or []
                # normalize
                for k, v in hdrs_list:
                    resp_headers[k.decode().lower()] = v.decode()
                await send(message)
            elif message["type"] == "http.response.body":
                resp_body += message.get("body", b"")
                await send(message)
            else:
                await send(message)

        # Recreate a receive function that replays the captured body to the app
        body_buffer = full_body
        async def receive_wrapper() -> Message:
            nonlocal body_buffer
            chunk = body_buffer
            body_buffer = b""
            return {"type": "http.request", "body": chunk, "more_body": False}

        await self.app(scope, receive_wrapper, send_wrapper)

        # Persist successful responses (<500)
        if resp_status < 500:
            try:
                body_to_store: object
                try:
                    body_to_store = json.loads(resp_body.decode() or "null")
                except Exception:
                    body_to_store = resp_body.decode(errors="ignore")

                async with SessionLocal() as session:
                    await session.execute(
                        text("""
                            INSERT INTO idempotency_keys(project_id, key, request_hash, status, headers, body)
                            VALUES (:p, :k, :h, :s, :headers, :body)
                            ON CONFLICT (project_id, key) DO NOTHING
                        """),
                        {
                            "p": project_id,
                            "k": idem_key,
                            "h": req_hash,
                            "s": resp_status,
                            "headers": json.dumps(resp_headers),
                            "body": json.dumps(body_to_store) if isinstance(body_to_store, (dict, list, type(None))) else body_to_store,
                        },
                    )
                    await session.commit()
            except Exception:
                # best-effort; never block the actual response
                pass

    @staticmethod
    def _header(scope: Scope, name: bytes) -> str | None:
        headers = dict((k.lower(), v) for k, v in (scope.get("headers") or []))
        v = headers.get(name)
        return v.decode() if v else None