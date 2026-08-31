"""`X-API-KEY` authentication for this API (not for LinkedIn)."""

from __future__ import annotations

import secrets

from fastapi import HTTPException, Security, status
from fastapi.security import APIKeyHeader

from app.config import get_settings

API_KEY_HEADER_NAME = "X-API-KEY"

# auto_error=False so an absent header reaches us and we control the message.
# Declaring it as a security scheme also gives /docs an Authorize button.
_api_key_scheme = APIKeyHeader(
    name=API_KEY_HEADER_NAME,
    auto_error=False,
    description="Required when the server is configured with an API_KEY.",
)


def require_api_key(api_key: str | None = Security(_api_key_scheme)) -> None:
    """Enforce `X-API-KEY` when an `API_KEY` is configured; no-op otherwise.

    Leaving `API_KEY` unset disables auth, which keeps local development friction-free
    and is the documented behaviour — a deployed instance must always set it.
    """
    settings = get_settings()
    if not settings.auth_enforced:
        return

    expected = settings.API_KEY.strip()
    # Constant-time compare so a wrong key cannot be recovered by timing.
    if not api_key or not secrets.compare_digest(api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Missing or invalid {API_KEY_HEADER_NAME} header.",
        )
