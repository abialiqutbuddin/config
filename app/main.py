# app/main.py
from __future__ import annotations
from fastapi import FastAPI, Request, Depends
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.logger import setup_logging
from app.core.middleware import IdempotencyMiddleware
from app.core.security import verify_jwt_token, mint_dev_token
from app.core.settings import settings
from app.api import (
    config,
    subscriptions,
    usage,
    invoices,
    stripe_webhooks,
    health,
    entitlements
)

setup_logging()

app = FastAPI(title="Config-driven Subscription Service", version="1.0.0")

# --- CORS ---
allow_origins = ["*"] if settings.CORS_ORIGINS == "*" else [o.strip() for o in settings.CORS_ORIGINS.split(",")]
allow_methods = ["*"] if settings.CORS_ALLOW_METHODS == "*" else [m.strip() for m in settings.CORS_ALLOW_METHODS.split(",")]
allow_headers = ["*"] if settings.CORS_ALLOW_HEADERS == "*" else [h.strip() for h in settings.CORS_ALLOW_HEADERS.split(",")]

app.add_middleware(
    CORSMiddleware,
    allow_origins=allow_origins,
    allow_credentials=settings.CORS_ALLOW_CREDENTIALS,
    allow_methods=allow_methods,
    allow_headers=allow_headers,
)

# --- Idempotency ---
app.add_middleware(IdempotencyMiddleware)

# --- Routers ---
app.include_router(config.router)
app.include_router(subscriptions.router)
app.include_router(usage.router)
app.include_router(invoices.router)
app.include_router(stripe_webhooks.router)
app.include_router(health.router)
app.include_router(entitlements.router) 

# Paths that bypass auth (health, docs, and Stripe webhook)
AUTH_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/docs/oauth2-redirect",
    "/stripe/webhook",
    "/favicon.ico",
)

@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    # CORS preflight
    if request.method == "OPTIONS":
        return await call_next(request)

    path = request.url.path
    if any(path.startswith(p) for p in AUTH_EXEMPT_PREFIXES):
        return await call_next(request)

    # Require JWT + project header
    auth_header = request.headers.get("Authorization")
    project_id = request.headers.get("X-Project-Id")

    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)
    if not project_id:
        return JSONResponse({"error": "Missing X-Project-Id header"}, status_code=422)

    token = auth_header.split("Bearer ", 1)[1].strip()
    try:
        claims = verify_jwt_token(token)
        request.state.auth = {"claims": claims, "project_id": project_id}
    except Exception as e:
        return JSONResponse({"error": f"Unauthorized: {e}"}, status_code=401)

    return await call_next(request)

# --- Dev-only token minting (for Postman) ---
if settings.DEV_MODE:
    from fastapi import APIRouter, Query
    dev_auth = APIRouter(prefix="/auth", tags=["Auth (dev)"])

    @dev_auth.get("/dev-token")
    def get_dev_token(sub: str = Query(default=None), ttl: int = Query(default=3600)):
        """
        Mint a short-lived JWT for local testing.
        """
        token = mint_dev_token(sub=sub, ttl_seconds=ttl)
        return {"token": token, "expiresIn": ttl}

    app.include_router(dev_auth)