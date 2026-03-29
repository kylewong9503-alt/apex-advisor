from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.industry import (
    ConflictFlag,
    IndustryDigestStatus,
    IndustrySourceType,
    ReminderFrequency,
)


class IndustryInputCreate(BaseModel):
    raw_text: str = Field(..., min_length=1)
    source_type: IndustrySourceType


class IndustryInputBatchCreate(BaseModel):
    items: List[IndustryInputCreate] = Field(..., min_length=1)


class IndustryInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_text: str
    source_type: IndustrySourceType
    created_at: datetime


class ClassifyRequest(BaseModel):
    input_ids: List[UUID] = Field(..., min_length=1)


class DimensionConflict(BaseModel):
    dimension: str
    description: str


class IndustryDigestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: IndustryDigestStatus
    dimension_summaries: Dict[str, str]
    dimension_merge_modes: Dict[str, str]
    dimension_annotations: Dict[str, str]
    conflicts: List[DimensionConflict]
    source_input_ids: List[str]
    topic_materials_preview: List[Dict[str, Any]]
    data_cards: Dict[str, Any]
    created_at: datetime
    updated_at: datetime


class DigestConfirmBody(BaseModel):
    dimension_summaries: Optional[Dict[str, str]] = None
    dimension_merge_modes: Optional[Dict[str, str]] = None


class DataCardFields(BaseModel):
    value: str = ""
    source: str = ""
    as_of: str = ""


class IndustryItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dimension: str
    content: str
    source_type: IndustrySourceType
    conflict_flag: Optional[ConflictFlag] = None
    industry_input_id: Optional[UUID] = None
    data_card: Optional[Dict[str, Any]] = None
    highlight_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IndustryItemUpdate(BaseModel):
    content: str = Field(..., min_length=1)


class TopicMaterialCreate(BaseModel):
    title: str = Field(..., min_length=1)
    core_insight: str = ""
    data_support: str = ""
    tags: List[str] = Field(default_factory=list)


class TopicMaterialUpdate(BaseModel):
    title: Optional[str] = None
    core_insight: Optional[str] = None
    data_support: Optional[str] = None
    tags: Optional[List[str]] = None


class TopicMaterialResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    core_insight: str
    data_support: str
    tags: List[str]
    created_at: datetime
    updated_at: datetime


class ReminderCreate(BaseModel):
    dimension: str = Field(..., min_length=1)
    frequency: ReminderFrequency


class ReminderUpdate(BaseModel):
    frequency: Optional[ReminderFrequency] = None
    next_due_at: Optional[datetime] = None


class ReminderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dimension: str
    frequency: ReminderFrequency
    next_due_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IndustryKnowledgeBaseResponse(BaseModel):
    industry_profile_id: UUID
    intelligence_by_dimension: Dict[str, List[IndustryItemResponse]]
    pending_digest: Optional[IndustryDigestResponse] = None
    insights_banner: List[IndustryItemResponse] = Field(default_factory=list)
    topic_materials: List[TopicMaterialResponse] = Field(default_factory=list)
    update_reminders: List[ReminderResponse] = Field(default_factory=list)


class ConfirmIndustryDigestResult(BaseModel):
    industry_items: List[IndustryItemResponse]
    topic_materials: List[TopicMaterialResponse]
