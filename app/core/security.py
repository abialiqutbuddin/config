# app/core/security.py
from __future__ import annotations
import time
from typing import Optional, Dict, Any
import jwt
from fastapi import HTTPException, status
from app.core.settings import settings


def verify_jwt_token(token: str) -> Dict[str, Any]:
    options = {"require": ["exp"], "verify_signature": True}
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
            audience=settings.JWT_AUDIENCE if settings.JWT_AUDIENCE else None,
            issuer=settings.JWT_ISSUER if settings.JWT_ISSUER else None,
            options=options,
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token expired")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f"Invalid token: {e}")

def mint_dev_token(
    *,
    sub: Optional[str] = None,
    ttl_seconds: int = 3600,
    extra_claims: Optional[Dict[str, Any]] = None,
) -> str:
    """
    DEV ONLY: create a short-lived JWT for local testing.
    """
    now = int(time.time())
    payload: Dict[str, Any] = {
        "sub": sub or settings.DEV_JWT_SUBJECT,
        "iat": now,
        "exp": now + ttl_seconds,
    }
    if settings.JWT_ISSUER:
        payload["iss"] = settings.JWT_ISSUER
    if settings.JWT_AUDIENCE:
        payload["aud"] = settings.JWT_AUDIENCE
    if extra_claims:
        payload.update(extra_claims)
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)