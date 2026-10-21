"""Pydantic schemas for Moderation Service conforming strictly to OpenAPI 3.1 specification."""

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CreateReportRequest(BaseModel):
    """Payload for submitting an abuse report against thread, comment, or account."""

    target_type: str = Field(..., pattern=r"^(thread|comment|account)$")
    target_id: uuid.UUID
    reason: str = Field(
        ..., pattern=r"^(spam|abuse|harassment|wrong_category|misinformation|other)$"
    )
    details: str | None = Field(default=None, max_length=1000)


class ReportResponse(BaseModel):
    """Public report schema conforming to OpenAPI #/components/schemas/Report."""

    id: uuid.UUID
    target_type: str
    target_id: uuid.UUID
    reason: str
    details: str | None = None
    status: str
    resolution_note: str | None = None
    created_at: datetime


class ModerationActionRequest(BaseModel):
    """Payload for applying a moderation action on a report."""

    action: str = Field(
        ...,
        pattern=r"^(pin|lock|move_category|soft_delete|ban|unban|role_change|dismiss_report)$",
    )
    reason: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ProactiveModerationActionRequest(BaseModel):
    """Payload for applying a proactive moderation action without an associated report."""

    action: str = Field(
        ...,
        pattern=r"^(pin|lock|move_category|soft_delete|ban|unban|role_change)$",
    )
    target_type: str = Field(..., pattern=r"^(thread|comment|account)$")
    target_id: uuid.UUID
    reason: str = Field(..., min_length=1)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ModerationActionResponse(BaseModel):
    """Moderation action schema conforming to OpenAPI #/components/schemas/ModerationAction."""

    id: uuid.UUID
    report_id: uuid.UUID | None = None
    moderator_id: uuid.UUID
    action: str
    target_type: str
    target_id: uuid.UUID
    reason: str
    created_at: datetime
