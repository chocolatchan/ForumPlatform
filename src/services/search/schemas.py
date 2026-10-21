"""Pydantic schemas for Search Service."""

import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class SearchItem(BaseModel):
    """Search result item conforming to OpenAPI SearchItem schema."""

    thread_id: uuid.UUID
    title: str
    snippet: str
    category_id: uuid.UUID
    author_id: uuid.UUID
    created_at: datetime


class SearchPage(BaseModel):
    """Search results page conforming to OpenAPI SearchPage schema."""

    items: list[SearchItem]
    next_cursor: str | None = None


class SearchSuggestionsResponse(BaseModel):
    """Search suggestions list conforming to OpenAPI schema."""

    suggestions: list[str]


# Domain Event Schema for consumption
class ConsumedEventPayload(BaseModel):
    event_id: uuid.UUID = Field(default_factory=uuid.uuid4)
    event_type: str
    occurred_at: datetime | None = None
    producer: str | None = None
    data: dict = Field(default_factory=dict)
