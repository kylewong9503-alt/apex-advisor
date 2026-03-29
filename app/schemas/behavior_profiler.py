from typing import List, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class BehaviorProfilerStartBody(BaseModel):
    member_id: UUID


class BehaviorProfilerStartResponse(BaseModel):
    session_id: UUID
    greeting: str


class BehaviorProfilerChatBody(BaseModel):
    session_id: UUID
    user_message: str = Field(..., min_length=1)


class SuggestedBehaviorTagSchema(BaseModel):
    label: str
    category: str
    confidence: float = Field(ge=0, le=1)
    source: str = "ai"


class BehaviorProfilerChatResponse(BaseModel):
    reply: str
    suggested_tags: List[SuggestedBehaviorTagSchema] = []


class BehaviorProfilerFinalizeBody(BaseModel):
    session_id: UUID


class BehaviorProfilerFinalizeResponse(BaseModel):
    tags: List[SuggestedBehaviorTagSchema]
    update_mode: Literal["increment", "overwrite"]


class BehaviorTagDraft(BaseModel):
    label: str = Field(..., min_length=1)
    category: str = Field(..., min_length=1)
    confidence: float = Field(default=0.8, ge=0, le=1)
    source: Literal["ai", "manual"] = "manual"


class ConfirmBehaviorTagsBody(BaseModel):
    tags: List[BehaviorTagDraft]
    update_mode: Literal["increment", "overwrite"]
