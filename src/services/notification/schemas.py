"""Pydantic schemas for Notification Service."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class NotificationPreferencesRequest(BaseModel):
    in_app_enabled: bool | None = None
    email_enabled: bool | None = None


class NotificationPreferencesResponse(BaseModel):
    in_app_enabled: bool
    email_enabled: bool


class NotificationItemResponse(BaseModel):
    id: uuid.UUID
    type: str
    title: str
    body: str
    target_type: str | None = None
    target_id: uuid.UUID | None = None
    is_read: bool
    created_at: datetime


class NotificationPageResponse(BaseModel):
    items: list[NotificationItemResponse]
    next_cursor: str | None = None


class UnreadCountResponse(BaseModel):
    unread_count: int


# Domain Event Schema for consumption
class ConsumedEventPayload(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    occurred_at: datetime | None = None
    producer: str | None = None
    data: dict = Field(default_factory=dict)
