"""FastAPI authentication and CSRF dependencies for Search Service."""

import uuid
from dataclasses import dataclass
from typing import Any

import httpx
from fastapi import Depends, HTTPException, Request, status

from src.common.internal_auth import verify_internal_service_request
from src.services.search.config import search_settings
from src.services.user.security import verify_csrf_token


@dataclass
class SearchUser:
    """Authenticated user context for Search Service."""

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


async def get_current_user(request: Request) -> SearchUser:
    """Validate session cookie against User Service or resolve trusted identity."""
    cookie_name = search_settings.session_cookie_name
    session_token = request.cookies.get(cookie_name)

    # Check for trusted forwarded headers if gateway resolved it internally
    trusted_user_id = request.headers.get("X-Forwarded-User-ID")
    trusted_role = request.headers.get("X-Forwarded-Role")
    if trusted_user_id and trusted_role:
        try:
            return SearchUser(
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
    url = f"{search_settings.user_service_url.rstrip('/')}/auth/session"

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

    return SearchUser(
        id=uuid.UUID(user_id_raw),
        username=user_info.get("username", "user"),
        role=user_info.get("role", "member"),
        display_name=user_info.get("display_name"),
    )


async def get_optional_user(request: Request) -> SearchUser | None:
    """Optionally validate session cookie if present."""
    cookie_name = search_settings.session_cookie_name
    if not request.cookies.get(cookie_name) and not request.headers.get("X-Forwarded-User-ID"):
        return None
    try:
        return await get_current_user(request)
    except HTTPException:
        return None


def require_role(*roles: str):
    """Enforce caller has one of the allowed roles."""

    async def _role_checker(user: SearchUser = Depends(get_current_user)) -> SearchUser:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: requires one of {roles}, current: {user.role}",
            )
        return user

    return _role_checker


async def verify_csrf_header(request: Request) -> None:
    """Verify CSRF token for state-mutating requests (ADR-004)."""
    csrf_token = request.headers.get("X-CSRF-Token")
    if not csrf_token:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="CSRF token missing from X-CSRF-Token header.",
        )

    if not verify_csrf_token(csrf_token, search_settings.csrf_secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid CSRF token.",
        )


async def verify_internal_service_key(request: Request) -> None:
    """Verify internal service-to-service key for internal endpoints."""
    verify_internal_service_request(request, search_settings)
