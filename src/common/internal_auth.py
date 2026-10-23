"""Shared authentication policy for protected internal service endpoints."""

import hmac
from typing import Protocol

from fastapi import HTTPException, Request, status

INTERNAL_SERVICE_KEY_HEADER = "X-Internal-Service-Key"


class InternalAuthSettings(Protocol):
    """Settings required to verify internal service-to-service requests."""

    internal_service_secret: str
    internal_service_auth_bypass: bool


def verify_internal_service_request(
    request: Request,
    settings: InternalAuthSettings,
) -> None:
    """Verify the shared internal credential, failing closed by default.

    A missing header is accepted only when the explicit development/test bypass
    setting is enabled. A supplied credential is always verified.
    """
    provided_key = request.headers.get(INTERNAL_SERVICE_KEY_HEADER)
    if provided_key:
        expected_key = settings.internal_service_secret
        if not expected_key or not hmac.compare_digest(
            provided_key.encode("utf-8"), expected_key.encode("utf-8")
        ):
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Internal service key invalid.",
            )
        return

    if settings.internal_service_auth_bypass:
        return

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Internal service key missing.",
    )
