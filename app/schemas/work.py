from __future__ import annotations

from datetime import datetime
from typing import List, Optional
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.work import (
    ComplianceTrend,
    DimensionStatus,
    FollowUpStatus,
    ProjectStatus,
    WorkstreamStatus,
)


# Keys aligned with DeliverableStandard fields / DimensionReview.dimension
DELIVERABLE_DIMENSION_KEYS = ("format", "style", "framework", "perspective", "habit")


class DeliverableStandardCreate(BaseModel):
    work_scene: str = Field(..., min_length=1)
    format_requirements: Optional[str] = None
    style_requirements: Optional[str] = None
    thinking_framework: Optional[str] = None
    perspective_and_stance: Optional[str] = None
    habit_preferences: Optional[str] = None


class DeliverableStandardUpdate(BaseModel):
    work_scene: Optional[str] = None
    format_requirements: Optional[str] = None
    style_requirements: Optional[str] = None
    thinking_framework: Optional[str] = None
    perspective_and_stance: Optional[str] = None
    habit_preferences: Optional[str] = None


class DeliverableStandardResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    work_scene: str
    format_requirements: Optional[str] = None
    style_requirements: Optional[str] = None
    thinking_framework: Optional[str] = None
    perspective_and_stance: Optional[str] = None
    habit_preferences: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class DraftReviewRequest(BaseModel):
    draft_text: str = Field(..., min_length=10)


class DimensionReviewResult(BaseModel):
    dimension: str
    status: DimensionStatus
    details: str
    suggestions: Optional[str] = None


class ReviewReportResponse(BaseModel):
    review_history_id: UUID
    deliverable_standard_id: UUID
    reviewed_at: datetime
    overall_compliance: float
    dimensions: List[DimensionReviewResult]
    pattern_recomputed: bool


class ReviewHistoryEntry(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    reviewed_at: datetime
    overall_compliance: float
    dimensions: List[DimensionReviewResult]


class DeliverablePatternResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    deliverable_standard_id: UUID
    based_on_count: int
    persistent_issues: List[str]
    improving_dimensions: List[str]
    compliance_trend: ComplianceTrend
    trend_description: str
    created_at: datetime
    updated_at: datetime


class ProjectCreate(BaseModel):
    name: str = Field(..., min_length=1)
    status: ProjectStatus = ProjectStatus.IN_PROGRESS
    deadline: Optional[datetime] = None
    description: Optional[str] = None


class ProjectUpdate(BaseModel):
    name: Optional[str] = None
    status: Optional[ProjectStatus] = None
    deadline: Optional[datetime] = None
    description: Optional[str] = None


class ProjectResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: ProjectStatus
    deadline: Optional[datetime] = None
    description: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class MeetingNoteCreate(BaseModel):
    title: str = Field(..., min_length=1)
    meeting_date: datetime
    participants: Optional[str] = None
    content: str = Field(..., min_length=1)


class MeetingNoteUpdate(BaseModel):
    title: Optional[str] = None
    meeting_date: Optional[datetime] = None
    participants: Optional[str] = None
    content: Optional[str] = None


class MeetingNoteResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    meeting_date: datetime
    participants: Optional[str] = None
    content: str
    created_at: datetime
    updated_at: datetime


class WorkstreamCreate(BaseModel):
    name: str = Field(..., min_length=1)
    progress_description: Optional[str] = None
    goals_and_requirements: Optional[str] = None
    owner: Optional[str] = None
    status: WorkstreamStatus = WorkstreamStatus.IN_PROGRESS


class WorkstreamUpdate(BaseModel):
    name: Optional[str] = None
    progress_description: Optional[str] = None
    goals_and_requirements: Optional[str] = None
    owner: Optional[str] = None
    status: Optional[WorkstreamStatus] = None


class WorkstreamResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    progress_description: Optional[str] = None
    goals_and_requirements: Optional[str] = None
    owner: Optional[str] = None
    status: WorkstreamStatus
    created_at: datetime
    updated_at: datetime


class FollowUpItemCreate(BaseModel):
    description: str = Field(..., min_length=1)
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    status: FollowUpStatus = FollowUpStatus.PENDING
    meeting_note_id: Optional[UUID] = None
    workstream_id: Optional[UUID] = None


class FollowUpItemUpdate(BaseModel):
    description: Optional[str] = None
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    status: Optional[FollowUpStatus] = None
    meeting_note_id: Optional[UUID] = None
    workstream_id: Optional[UUID] = None


class FollowUpItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    description: str
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    status: FollowUpStatus
    meeting_note_id: Optional[UUID] = None
    workstream_id: Optional[UUID] = None
    created_at: datetime
    updated_at: datetime


class ExtractedFollowUpDraft(BaseModel):
    description: str
    owner: Optional[str] = None
    due_date: Optional[datetime] = None
    suggested_workstream_name: Optional[str] = None
    suggested_workstream_id: Optional[UUID] = None


class ExtractFollowUpsResponse(BaseModel):
    meeting_note_id: UUID
    drafts: List[ExtractedFollowUpDraft]


class ConfirmExtractedFollowUpsBody(BaseModel):
    items: List[ExtractedFollowUpDraft] = Field(..., min_length=1)


class WorkKnowledgeBaseResponse(BaseModel):
    work_profile_id: UUID
    deliverable_standards: List[DeliverableStandardResponse]
    projects: List[ProjectResponse]
    meeting_notes: List[MeetingNoteResponse]
    workstreams: List[WorkstreamResponse]
    follow_up_items: List[FollowUpItemResponse]
