from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PromptGenerateRequest(BaseModel):
    scene: str = Field(
        ...,
        description="One of: communication_report, meeting_presentation, materials_proposal, research, project_planning",
    )
    team_member_id: Optional[UUID] = None


class PromptGenerateResponse(BaseModel):
    scene: str
    scene_label_zh: str
    template: str
    placeholder_hints: List[str] = Field(default_factory=list)


class SavedPromptTemplateCreate(BaseModel):
    name: str = Field(..., min_length=1)
    scene: str
    body: str = Field(..., min_length=1)


class SavedPromptTemplateUpdate(BaseModel):
    name: Optional[str] = None
    body: Optional[str] = None


class SavedPromptTemplateResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    scene: str
    body: str
    version: int
    needs_refresh: bool = False
    last_synced_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime
