"""FastAPI authentication and CSRF dependencies for Interaction Service."""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status

from src.services.interaction.config import interaction_settings
from src.services.user.security import verify_csrf_token


@dataclass
class InteractionUser:
    """Authenticated user context for Interaction Service."""

    id: uuid.UUID
    username: str
    role: str
    display_name: str | None = None


# HTTP client hook for dependency injection / mocking in tests
_auth_http_client: httpx.AsyncClient | None = None


def get_auth_http_client() -> httpx.AsyncClient:
    global _auth_http_client
    if _auth_http_client is None:
        _auth_http_client = httpx.AsyncClient(timeout=5.0)
    return _auth_http_client


def set_auth_http_client(client: httpx.AsyncClient | None) -> None:
    global _auth_http_client
    _auth_http_client = client


async def get_current_user(request: Request) -> InteractionUser:
    """Validate session cookie against User Service or resolve trusted identity."""
    cookie_name = interaction_settings.session_cookie_name
    session_token = request.cookies.get(cookie_name)

    # Check for trusted forwarded headers if gateway resolved it internally
    trusted_user_id = request.headers.get("X-Forwarded-User-ID")
    trusted_role = request.headers.get("X-Forwarded-Role")
    if trusted_user_id and trusted_role:
        try:
            return InteractionUser(
                id=uuid.UUID(trusted_user_id),
                username=request.headers.get("X-Forwarded-Username", "user"),
                role=trusted_role,
            )
        except ValueError:
            pass

    if not session_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required: session cookie missing.",
        )

    client = get_auth_http_client()
    url = f"{interaction_settings.user_service_url.rstrip('/')}/auth/session"

    try:
        resp = await client.get(
            url,
            headers={
                "Cookie": f"{cookie_name}={session_token}",
                "X-Request-ID": request.headers.get("X-Request-ID", str(uuid.uuid4())),
            },
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Unable to reach User Service for authentication: {exc}",
        ) from exc

    if resp.status_code == 401:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session.",
        )
    elif resp.status_code == 403:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is banned or suspended.",
        )
    elif resp.status_code != 200:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication verification failed.",
        )

    data: dict[str, Any] = resp.json()
    user_info = data.get("user", {})
    return InteractionUser(
        id=uuid.UUID(user_info["id"]),
        username=user_info["username"],
        role=user_info.get("role", "member"),
        display_name=user_info.get("display_name"),
    )


def require_role(*allowed_roles: str):
    """Enforce role authorization on protected interaction endpoints."""

    def role_checker(user: InteractionUser = Depends(get_current_user)) -> InteractionUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: requires role in {allowed_roles}, your role is '{user.role}'.",
            )
        return user

    return role_checker


async def verify_csrf_header(request: Request) -> None:
    """Enforce synchronizer CSRF token check (ADR-004) on state-changing requests."""
    cookie_name = interaction_settings.session_cookie_name
    if cookie_name not in request.cookies:
        return

    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token or not verify_csrf_token(csrf_token, interaction_settings.csrf_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed or missing X-CSRF-Token header.",
        )
