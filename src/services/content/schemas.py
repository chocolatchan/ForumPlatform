"""Content Service Pydantic schemas conforming strictly to OpenAPI contract."""

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# =============================================================================
# Category Schemas
# =============================================================================
class CategoryResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None


class CategoryListResponse(BaseModel):
    items: list[CategoryResponse]


class CreateCategoryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    slug: str = Field(..., min_length=1, max_length=100)
    description: str | None = None
    sort_order: int = 0


class UpdateCategoryRequest(BaseModel):
    name: str | None = Field(default=None, max_length=80)
    slug: str | None = Field(default=None, max_length=100)
    description: str | None = None
    sort_order: int | None = None
    is_hidden: bool | None = None


# =============================================================================
# Thread Schemas
# =============================================================================
ThreadStatus = Literal["draft", "published", "hidden", "deleted"]


class CreateThreadRequest(BaseModel):
    category_id: uuid.UUID
    title: str = Field(..., min_length=10, max_length=150)
    body: str = Field(..., min_length=20)
    tags: list[str] = Field(default_factory=list, max_length=5)
    image_ids: list[uuid.UUID] = Field(default_factory=list)


class EditThreadRequest(BaseModel):
    title: str | None = Field(default=None, min_length=10, max_length=150)
    body: str | None = Field(default=None, min_length=20)
    tags: list[str] | None = Field(default=None, max_length=5)


class ThreadResponse(BaseModel):
    id: uuid.UUID
    author_id: uuid.UUID
    category_id: uuid.UUID
    title: str
    body: str
    tags: list[str] = Field(default_factory=list)
    status: str
    is_pinned: bool = False
    is_locked: bool = False
    best_answer_comment_id: uuid.UUID | None = None
    view_count: int = 0
    created_at: datetime
    updated_at: datetime


class ThreadPageResponse(BaseModel):
    items: list[ThreadResponse]
    next_cursor: str | None = None


class LockThreadRequest(BaseModel):
    is_locked: bool


class PinThreadRequest(BaseModel):
    is_pinned: bool


class BestAnswerRequest(BaseModel):
    comment_id: uuid.UUID


# =============================================================================
# Comment Schemas
# =============================================================================
class CreateCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)
    parent_comment_id: uuid.UUID | None = None


class EditCommentRequest(BaseModel):
    body: str = Field(..., min_length=1)


class CommentResponse(BaseModel):
    id: uuid.UUID
    thread_id: uuid.UUID
    author_id: uuid.UUID
    parent_comment_id: uuid.UUID | None = None
    body: str
    is_deleted: bool = False
    created_at: datetime


class CommentPageResponse(BaseModel):
    items: list[CommentResponse]
    next_cursor: str | None = None


# =============================================================================
# Attachment Schemas
# =============================================================================
class AttachmentResponse(BaseModel):
    id: uuid.UUID
    object_key: str
    file_size: int
    mime_type: str
    status: str
    created_at: datetime

