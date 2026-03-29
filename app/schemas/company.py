from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.company import DigestStatus, SourceType


class IntelligenceInputCreate(BaseModel):
    raw_text: str = Field(..., min_length=1)
    source_type: SourceType


class IntelligenceInputBatchCreate(BaseModel):
    items: List[IntelligenceInputCreate] = Field(..., min_length=1)


class IntelligenceInputResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    raw_text: str
    source_type: SourceType
    created_at: datetime


class ClassifyRequest(BaseModel):
    input_ids: List[UUID] = Field(..., min_length=1)


class DimensionConflict(BaseModel):
    dimension: str
    description: str


class IntelligenceDigestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: DigestStatus
    dimension_summaries: Dict[str, str]
    dimension_merge_modes: Dict[str, str]
    conflicts: List[DimensionConflict]
    source_input_ids: List[str]
    created_at: datetime
    updated_at: datetime


class DigestConfirmBody(BaseModel):
    """Optional overrides before merge; omitted dimensions keep digest values."""

    dimension_summaries: Optional[Dict[str, str]] = None
    dimension_merge_modes: Optional[Dict[str, Literal["supplement", "override"]]] = None


class IntelligenceItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    dimension: str
    content: str
    source_type: SourceType
    intelligence_input_id: Optional[UUID] = None
    highlight_until: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime


class IntelligenceItemUpdate(BaseModel):
    content: str = Field(..., min_length=1)


class StakeholderCreate(BaseModel):
    name: str = Field(..., min_length=1)
    department: Optional[str] = None
    influence_description: Optional[str] = None


class StakeholderUpdate(BaseModel):
    name: Optional[str] = None
    department: Optional[str] = None
    influence_description: Optional[str] = None


class StakeholderResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    department: Optional[str] = None
    influence_description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class CompanyKnowledgeBaseResponse(BaseModel):
    """Snapshot for Layer 2 UI: dimensions, stakeholders, pending digest, banner."""

    company_profile_id: UUID
    intelligence_by_dimension: Dict[str, List[IntelligenceItemResponse]]
    pending_digest: Optional[IntelligenceDigestResponse] = None
    insights_banner: List[IntelligenceItemResponse] = Field(default_factory=list)
    stakeholders: List[StakeholderResponse] = Field(default_factory=list)
