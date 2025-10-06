from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.core.logger import setup_logging
from app.core.middleware import IdempotencyMiddleware
from app.core.security import verify_jwt_token
from app.api import (
    config,
    subscriptions,
    usage,
    invoices,
    stripe_webhooks,
    health,
)

setup_logging()

app = FastAPI(title="Config-driven Subscription Service", version="1.0.0")

# CORS (adjust as needed)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],   # tighten in prod
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Idempotency
app.add_middleware(IdempotencyMiddleware)

# Routers
app.include_router(config.router)
app.include_router(subscriptions.router)
app.include_router(usage.router)
app.include_router(invoices.router)
app.include_router(stripe_webhooks.router)
app.include_router(health.router)

# Paths that should bypass auth (health, docs, and Stripe webhook)
AUTH_EXEMPT_PREFIXES = (
    "/health",
    "/docs",
    "/redoc",
    "/openapi.json",
    "/stripe/webhook",
)

@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    # Skip auth for exempt routes
    for p in AUTH_EXEMPT_PREFIXES:
        if request.url.path.startswith(p):
            return await call_next(request)

    auth_header = request.headers.get("Authorization")
    project_id = request.headers.get("X-Project-Id")

    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse({"error": "Missing or invalid Authorization header"}, status_code=401)
    if not project_id:
        return JSONResponse({"error": "Missing X-Project-Id header"}, status_code=422)

    token = auth_header.split("Bearer ", 1)[1].strip()
    try:
        claims = verify_jwt_token(token)
        # Make claims + project_id available to downstream handlers
        request.state.auth = {"claims": claims, "project_id": project_id}
    except Exception as e:
        return JSONResponse({"error": f"Unauthorized: {e}"}, status_code=401)

    return await call_next(request)