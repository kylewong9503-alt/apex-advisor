from __future__ import annotations

import uuid
from typing import List

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.user import User
from app.models.work import (
    DeliverablePattern,
    DeliverableStandard,
    DimensionReview,
    FollowUpItem,
    FollowUpStatus,
    MeetingNote,
    Project,
    ReviewHistory,
    WorkProfile,
    Workstream,
)
from app.schemas.work import (
    ConfirmExtractedFollowUpsBody,
    DeliverablePatternResponse,
    DeliverableStandardCreate,
    DeliverableStandardResponse,
    DeliverableStandardUpdate,
    DimensionReviewResult,
    DraftReviewRequest,
    ExtractFollowUpsResponse,
    ExtractedFollowUpDraft,
    FollowUpItemCreate,
    FollowUpItemResponse,
    FollowUpItemUpdate,
    MeetingNoteCreate,
    MeetingNoteResponse,
    MeetingNoteUpdate,
    ProjectCreate,
    ProjectResponse,
    ProjectUpdate,
    ReviewHistoryEntry,
    ReviewReportResponse,
    WorkKnowledgeBaseResponse,
    WorkstreamCreate,
    WorkstreamResponse,
    WorkstreamUpdate,
)
from app.services.ai.deliverable_review import DeliverableReviewService, load_pattern_json
from app.services.ai.followup_extractor import FollowUpExtractor

router = APIRouter()
_review = DeliverableReviewService()
_extractor = FollowUpExtractor()


def _wp(db: Session, user: User) -> WorkProfile:
    if not user.work_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Work profile not found",
        )
    return user.work_profile


def _dim_results(rows: List[DimensionReview]) -> List[DimensionReviewResult]:
    out: List[DimensionReviewResult] = []
    for dr in rows:
        out.append(
            DimensionReviewResult(
                dimension=dr.dimension,
                status=dr.status,
                details=dr.details,
                suggestions=dr.suggestions,
            )
        )
    return out


@router.get("/knowledge-base", response_model=WorkKnowledgeBaseResponse)
def work_knowledge_base(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    return WorkKnowledgeBaseResponse(
        work_profile_id=wp.id,
        deliverable_standards=[
            DeliverableStandardResponse.model_validate(s) for s in wp.deliverable_standards
        ],
        projects=[ProjectResponse.model_validate(p) for p in wp.projects],
        meeting_notes=[MeetingNoteResponse.model_validate(n) for n in wp.meeting_notes],
        workstreams=[WorkstreamResponse.model_validate(w) for w in wp.workstreams],
        follow_up_items=[FollowUpItemResponse.model_validate(f) for f in wp.follow_up_items],
    )


# --- Deliverable standards ---


@router.post(
    "/deliverable-standards",
    response_model=DeliverableStandardResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_deliverable_standard(
    body: DeliverableStandardCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = DeliverableStandard(
        work_profile_id=wp.id,
        work_scene=body.work_scene.strip(),
        format_requirements=body.format_requirements,
        style_requirements=body.style_requirements,
        thinking_framework=body.thinking_framework,
        perspective_and_stance=body.perspective_and_stance,
        habit_preferences=body.habit_preferences,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/deliverable-standards", response_model=list[DeliverableStandardResponse])
def list_deliverable_standards(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    return wp.deliverable_standards


@router.get("/deliverable-standards/{standard_id}", response_model=DeliverableStandardResponse)
def get_deliverable_standard(
    standard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(DeliverableStandard)
        .filter(
            DeliverableStandard.id == standard_id,
            DeliverableStandard.work_profile_id == wp.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return row


@router.patch("/deliverable-standards/{standard_id}", response_model=DeliverableStandardResponse)
def update_deliverable_standard(
    standard_id: uuid.UUID,
    body: DeliverableStandardUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(DeliverableStandard)
        .filter(
            DeliverableStandard.id == standard_id,
            DeliverableStandard.work_profile_id == wp.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if "work_scene" in data and data["work_scene"] is not None:
        data["work_scene"] = data["work_scene"].strip()
        if not data["work_scene"]:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="work_scene is required",
            )
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/deliverable-standards/{standard_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_deliverable_standard(
    standard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(DeliverableStandard)
        .filter(
            DeliverableStandard.id == standard_id,
            DeliverableStandard.work_profile_id == wp.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


@router.post("/deliverable-standards/{standard_id}/review", response_model=ReviewReportResponse)
async def review_draft(
    standard_id: uuid.UUID,
    body: DraftReviewRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        rh, pattern_recomputed = await _review.review_draft(
            db,
            user_id=current_user.id,
            deliverable_standard_id=standard_id,
            draft_text=body.draft_text,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(e),
        ) from e
    dims = (
        db.query(DimensionReview)
        .filter(DimensionReview.review_history_id == rh.id)
        .order_by(DimensionReview.dimension.asc())
        .all()
    )
    return ReviewReportResponse(
        review_history_id=rh.id,
        deliverable_standard_id=standard_id,
        reviewed_at=rh.reviewed_at,
        overall_compliance=rh.overall_compliance,
        dimensions=_dim_results(dims),
        pattern_recomputed=pattern_recomputed,
    )


@router.get(
    "/deliverable-standards/{standard_id}/review-history",
    response_model=list[ReviewHistoryEntry],
)
def list_review_history(
    standard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    std = (
        db.query(DeliverableStandard)
        .filter(
            DeliverableStandard.id == standard_id,
            DeliverableStandard.work_profile_id == wp.id,
        )
        .first()
    )
    if not std:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    histories = (
        db.query(ReviewHistory)
        .filter(ReviewHistory.deliverable_standard_id == std.id)
        .order_by(ReviewHistory.reviewed_at.desc())
        .all()
    )
    out: List[ReviewHistoryEntry] = []
    for h in histories:
        dims = (
            db.query(DimensionReview)
            .filter(DimensionReview.review_history_id == h.id)
            .order_by(DimensionReview.dimension.asc())
            .all()
        )
        out.append(
            ReviewHistoryEntry(
                id=h.id,
                reviewed_at=h.reviewed_at,
                overall_compliance=h.overall_compliance,
                dimensions=_dim_results(dims),
            )
        )
    return out


@router.get(
    "/deliverable-standards/{standard_id}/pattern",
    response_model=DeliverablePatternResponse | None,
)
def get_deliverable_pattern(
    standard_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    std = (
        db.query(DeliverableStandard)
        .filter(
            DeliverableStandard.id == standard_id,
            DeliverableStandard.work_profile_id == wp.id,
        )
        .first()
    )
    if not std:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    pat = (
        db.query(DeliverablePattern)
        .filter(DeliverablePattern.deliverable_standard_id == std.id)
        .order_by(DeliverablePattern.updated_at.desc())
        .first()
    )
    if not pat:
        return None
    return DeliverablePatternResponse(
        id=pat.id,
        deliverable_standard_id=pat.deliverable_standard_id,
        based_on_count=pat.based_on_count,
        persistent_issues=load_pattern_json(pat.persistent_issues),
        improving_dimensions=load_pattern_json(pat.improving_dimensions),
        compliance_trend=pat.compliance_trend,
        trend_description=pat.trend_description,
        created_at=pat.created_at,
        updated_at=pat.updated_at,
    )


# --- Projects ---


@router.post("/projects", response_model=ProjectResponse, status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = Project(
        work_profile_id=wp.id,
        name=body.name.strip(),
        status=body.status,
        deadline=body.deadline,
        description=body.description,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/projects", response_model=list[ProjectResponse])
def list_projects(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    return wp.projects


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: uuid.UUID,
    body: ProjectUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(Project)
        .filter(Project.id == project_id, Project.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/projects/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    project_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(Project)
        .filter(Project.id == project_id, Project.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


# --- Meeting notes ---


@router.post("/meeting-notes", response_model=MeetingNoteResponse, status_code=status.HTTP_201_CREATED)
def create_meeting_note(
    body: MeetingNoteCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = MeetingNote(
        work_profile_id=wp.id,
        title=body.title.strip(),
        meeting_date=body.meeting_date,
        participants=body.participants,
        content=body.content.strip(),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/meeting-notes", response_model=list[MeetingNoteResponse])
def list_meeting_notes(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    return sorted(wp.meeting_notes, key=lambda n: n.meeting_date, reverse=True)


@router.patch("/meeting-notes/{note_id}", response_model=MeetingNoteResponse)
def update_meeting_note(
    note_id: uuid.UUID,
    body: MeetingNoteUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(MeetingNote)
        .filter(MeetingNote.id == note_id, MeetingNote.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if "title" in data and data["title"] is not None:
        data["title"] = data["title"].strip()
    if "content" in data and data["content"] is not None:
        data["content"] = data["content"].strip()
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/meeting-notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_meeting_note(
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(MeetingNote)
        .filter(MeetingNote.id == note_id, MeetingNote.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


@router.post(
    "/meeting-notes/{note_id}/extract-followups",
    response_model=ExtractFollowUpsResponse,
)
async def extract_followups(
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        drafts = await _extractor.extract_drafts(db, user_id=current_user.id, meeting_note_id=note_id)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    items = [ExtractedFollowUpDraft.model_validate(d) for d in drafts]
    return ExtractFollowUpsResponse(meeting_note_id=note_id, drafts=items)


@router.post(
    "/meeting-notes/{note_id}/confirm-followups",
    response_model=list[FollowUpItemResponse],
)
def confirm_extracted_followups(
    note_id: uuid.UUID,
    body: ConfirmExtractedFollowUpsBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    try:
        rows = _extractor.persist_confirmed(
            db,
            user_id=current_user.id,
            meeting_note_id=note_id,
            items=[d.model_dump() for d in body.items],
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return rows


# --- Workstreams ---


@router.post("/workstreams", response_model=WorkstreamResponse, status_code=status.HTTP_201_CREATED)
def create_workstream(
    body: WorkstreamCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = Workstream(
        work_profile_id=wp.id,
        name=body.name.strip(),
        progress_description=body.progress_description,
        goals_and_requirements=body.goals_and_requirements,
        owner=body.owner,
        status=body.status,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/workstreams", response_model=list[WorkstreamResponse])
def list_workstreams(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    return wp.workstreams


@router.patch("/workstreams/{ws_id}", response_model=WorkstreamResponse)
def update_workstream(
    ws_id: uuid.UUID,
    body: WorkstreamUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(Workstream)
        .filter(Workstream.id == ws_id, Workstream.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        data["name"] = data["name"].strip()
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/workstreams/{ws_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_workstream(
    ws_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(Workstream)
        .filter(Workstream.id == ws_id, Workstream.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


# --- Follow-up items ---


@router.post("/follow-ups", response_model=FollowUpItemResponse, status_code=status.HTTP_201_CREATED)
def create_follow_up(
    body: FollowUpItemCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = FollowUpItem(
        work_profile_id=wp.id,
        description=body.description.strip(),
        owner=body.owner,
        due_date=body.due_date,
        status=body.status,
        meeting_note_id=body.meeting_note_id,
        workstream_id=body.workstream_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/follow-ups", response_model=list[FollowUpItemResponse])
def list_follow_ups(
    status_filter: str | None = Query(None, alias="status"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    q = db.query(FollowUpItem).filter(FollowUpItem.work_profile_id == wp.id)
    if status_filter in ("pending", "completed"):
        q = q.filter(FollowUpItem.status == FollowUpStatus(status_filter))
    rows = q.order_by(FollowUpItem.due_date.asc()).all()
    return rows


@router.patch("/follow-ups/{item_id}", response_model=FollowUpItemResponse)
def update_follow_up(
    item_id: uuid.UUID,
    body: FollowUpItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(FollowUpItem)
        .filter(FollowUpItem.id == item_id, FollowUpItem.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    for k, v in body.model_dump(exclude_unset=True).items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/follow-ups/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_follow_up(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    wp = _wp(db, current_user)
    row = (
        db.query(FollowUpItem)
        .filter(FollowUpItem.id == item_id, FollowUpItem.work_profile_id == wp.id)
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None
