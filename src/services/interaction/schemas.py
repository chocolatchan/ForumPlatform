"""Interaction Service Pydantic schemas conforming strictly to OpenAPI contract."""

import uuid
from typing import Literal

from pydantic import BaseModel, Field


class VoteRequest(BaseModel):
    value: int = Field(..., description="Vote value: 1 for upvote or -1 for downvote")


class VoteResponse(BaseModel):
    target_type: Literal["thread", "comment"]
    target_id: uuid.UUID
    my_vote: int | None
    score: int
    reputation_delta: int = 0


class BookmarkResponse(BaseModel):
    bookmarked: bool


class ReputationResponse(BaseModel):
    user_id: uuid.UUID
    reputation_score: int
