from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
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

# Middlewares
from app.core.middleware import IdempotencyMiddleware
app.add_middleware(IdempotencyMiddleware)

# Routers
app.include_router(config.router)
app.include_router(subscriptions.router)
app.include_router(usage.router)
app.include_router(invoices.router)
app.include_router(stripe_webhooks.router)
app.include_router(health.router)


@app.middleware("http")
async def authenticate_request(request: Request, call_next):
    if request.url.path.startswith("/health"):
        return await call_next(request)

    auth_header = request.headers.get("Authorization")
    project_id = request.headers.get("X-Project-Id")

    if not auth_header or not auth_header.startswith("Bearer "):
        return JSONResponse(
            {"error": "Missing or invalid Authorization header"}, status_code=401
        )
    if not project_id:
        return JSONResponse(
            {"error": "Missing X-Project-Id header"}, status_code=422
        )

    token = auth_header.split("Bearer ")[1]
    try:
        verify_jwt_token(token)
    except Exception as e:
        return JSONResponse({"error": f"Unauthorized: {e}"}, status_code=401)

    response = await call_next(request)
    return response