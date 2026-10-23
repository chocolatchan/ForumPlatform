"""FastAPI authentication and CSRF dependencies for Notification Service."""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status

from src.common.internal_auth import verify_internal_service_request
from src.services.notification.config import notification_settings
from src.services.user.security import verify_csrf_token


@dataclass
class NotificationUser:
    """Authenticated user context for Notification Service."""

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


async def get_current_user(request: Request) -> NotificationUser:
    """Validate session cookie against User Service or resolve trusted identity."""
    cookie_name = notification_settings.session_cookie_name
    session_token = request.cookies.get(cookie_name)

    # Check for trusted forwarded headers if gateway resolved it internally
    trusted_user_id = request.headers.get("X-Forwarded-User-ID")
    trusted_role = request.headers.get("X-Forwarded-Role")
    if trusted_user_id and trusted_role:
        try:
            return NotificationUser(
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
    url = f"{notification_settings.user_service_url.rstrip('/')}/auth/session"

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
            detail=f"User Service unreachable: {exc}",
        ) from exc

    if resp.status_code == status.HTTP_401_UNAUTHORIZED:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired session.",
        )

    if resp.status_code != status.HTTP_200_OK:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session validation failed.",
        )

    data: dict[str, Any] = resp.json()
    user_info = data.get("user") or data
    user_id_raw = user_info.get("id") or user_info.get("user_id")
    if not user_id_raw:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session response missing user identity.",
        )

    return NotificationUser(
        id=uuid.UUID(str(user_id_raw)),
        username=user_info.get("username", "user"),
        role=user_info.get("role", "member"),
        display_name=user_info.get("display_name"),
    )


async def get_optional_user(request: Request) -> NotificationUser | None:
    """Optionally resolve current authenticated user if session cookie is present."""
    cookie_name = notification_settings.session_cookie_name
    if not request.cookies.get(cookie_name) and not request.headers.get("X-Forwarded-User-ID"):
        return None
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


def require_role(*allowed_roles: str):
    """Enforce role authorization dependency."""

    async def _role_checker(user: NotificationUser = Depends(get_current_user)) -> NotificationUser:
        if user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Operation requires one of roles: {', '.join(allowed_roles)}.",
            )
        return user

    return _role_checker


async def verify_csrf_header(request: Request) -> None:
    """Enforce synchronizer CSRF token check (ADR-004) on state-changing requests."""
    cookie_name = notification_settings.session_cookie_name
    if cookie_name not in request.cookies:
        return

    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token or not verify_csrf_token(csrf_token, notification_settings.csrf_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token validation failed or missing X-CSRF-Token header.",
        )


async def verify_internal_service_key(request: Request) -> None:
    """Verify internal service-to-service key for internal endpoints."""
    verify_internal_service_request(request, notification_settings)
