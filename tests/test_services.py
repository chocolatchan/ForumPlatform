"""Tests for the 6 Service FastAPI app health endpoints."""

import pytest
from httpx import ASGITransport, AsyncClient

from src.services.content.app import app as content_app
from src.services.interaction.app import app as interaction_app
from src.services.moderation.app import app as moderation_app
from src.services.notification.app import app as notification_app
from src.services.search.app import app as search_app
from src.services.user.app import app as user_app


@pytest.mark.parametrize(
    "app_instance",
    [
        user_app,
        content_app,
        interaction_app,
        moderation_app,
        notification_app,
        search_app,
    ],
)
@pytest.mark.asyncio
async def test_service_health_endpoints(app_instance):
    transport = ASGITransport(app=app_instance)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Liveness check does not touch database
        resp = await client.get("/health/live")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
