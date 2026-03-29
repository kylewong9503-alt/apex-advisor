from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.team import ReportingPreference, TagSource


class BehaviorTagResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    label: str
    category: str
    confidence: float
    source: TagSource
    created_at: datetime
    updated_at: datetime


class TeamMemberCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    role: str = Field(..., min_length=1, max_length=255)

    @field_validator("name", "role")
    @classmethod
    def strip_required(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("must not be empty or whitespace")
        return s
    personality_traits: Optional[str] = None
    communication_preference: Optional[str] = None
    decision_style: Optional[str] = None
    reporting_preference: Optional[ReportingPreference] = None
    taboo: Optional[str] = None
    notes: Optional[str] = None


class TeamMemberUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    role: Optional[str] = Field(None, min_length=1, max_length=255)

    @field_validator("name", "role")
    @classmethod
    def strip_optional(cls, v: Optional[str]) -> Optional[str]:
        if v is None:
            return v
        s = v.strip()
        if not s:
            raise ValueError("must not be empty or whitespace")
        return s
    personality_traits: Optional[str] = None
    communication_preference: Optional[str] = None
    decision_style: Optional[str] = None
    reporting_preference: Optional[ReportingPreference] = None
    taboo: Optional[str] = None
    notes: Optional[str] = None


class TeamMemberResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True, use_enum_values=True)

    id: UUID
    user_id: UUID
    name: str
    role: str
    personality_traits: Optional[str] = None
    communication_preference: Optional[str] = None
    decision_style: Optional[str] = None
    reporting_preference: Optional[ReportingPreference] = None
    taboo: Optional[str] = None
    notes: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    behavior_tags: List[BehaviorTagResponse] = []
