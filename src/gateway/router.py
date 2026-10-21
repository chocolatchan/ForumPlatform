"""Gateway route resolution mapping public paths to upstream services."""

import re
from typing import NamedTuple

from src.gateway.config import GatewaySettings


class UpstreamRoute(NamedTuple):
    service_name: str
    target_base_url: str
    upstream_path: str


class GatewayRouter:
    """Resolves incoming API paths to the appropriate upstream microservice."""

    def __init__(self, settings: GatewaySettings) -> None:
        self.settings = settings
        # Patterns ordered from most specific to least specific
        self.rules: list[tuple[re.Pattern[str], str, str]] = [
            # 1. Interaction Service specific routes (must precede content/user general routes)
            (
                re.compile(r"^profile/bookmarks(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            (
                re.compile(r"^users/[^/]+/reputation(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            (
                re.compile(r"^threads/[^/]+/vote(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            (
                re.compile(r"^threads/[^/]+/bookmark(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            (
                re.compile(r"^threads/[^/]+/view(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            (
                re.compile(r"^comments/[^/]+/vote(/.*)?$"),
                "interaction",
                settings.interaction_service_url,
            ),
            # 2. User Service routes
            (re.compile(r"^auth(/.*)?$"), "user", settings.user_service_url),
            (re.compile(r"^profile(/.*)?$"), "user", settings.user_service_url),
            (re.compile(r"^admin/users(/.*)?$"), "user", settings.user_service_url),
            (re.compile(r"^users(/.*)?$"), "user", settings.user_service_url),
            # 3. Content Service routes
            (re.compile(r"^categories(/.*)?$"), "content", settings.content_service_url),
            (re.compile(r"^admin/categories(/.*)?$"), "content", settings.content_service_url),
            (re.compile(r"^threads(/.*)?$"), "content", settings.content_service_url),
            (re.compile(r"^comments(/.*)?$"), "content", settings.content_service_url),
            (re.compile(r"^uploads(/.*)?$"), "content", settings.content_service_url),
            # 4. Moderation Service routes
            (re.compile(r"^reports(/.*)?$"), "moderation", settings.moderation_service_url),
            (re.compile(r"^moderation(/.*)?$"), "moderation", settings.moderation_service_url),
            # 5. Notification Service routes
            (
                re.compile(r"^notifications(/.*)?$"),
                "notification",
                settings.notification_service_url,
            ),
            # 6. Search Service routes
            (re.compile(r"^search(/.*)?$"), "search", settings.search_service_url),
        ]

    def resolve(self, path: str) -> UpstreamRoute | None:
        """Resolve a public path (stripped of /api/v1 prefix) to an upstream route."""
        clean_path = path.lstrip("/")
        for pattern, service_name, base_url in self.rules:
            if pattern.match(clean_path):
                return UpstreamRoute(
                    service_name=service_name,
                    target_base_url=base_url.rstrip("/"),
                    upstream_path=f"/{clean_path}",
                )
        return None
