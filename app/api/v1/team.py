import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.team import TeamMember
from app.models.user import User
from app.schemas.behavior_profiler import ConfirmBehaviorTagsBody
from app.schemas.team import (
    BehaviorTagResponse,
    TeamMemberCreate,
    TeamMemberResponse,
    TeamMemberUpdate,
)
from app.services.ai.behavior_profiler import persist_behavior_tags

router = APIRouter()


def _member_for_user(
    db: Session, user_id: uuid.UUID, member_id: uuid.UUID
) -> TeamMember:
    member = (
        db.query(TeamMember)
        .filter(TeamMember.id == member_id, TeamMember.user_id == user_id)
        .first()
    )
    if not member:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Team member not found",
        )
    return member


@router.get("/members", response_model=list[TeamMemberResponse])
def list_team_members(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    members = (
        db.query(TeamMember)
        .filter(TeamMember.user_id == current_user.id)
        .order_by(TeamMember.created_at.asc())
        .all()
    )
    return members


@router.post(
    "/members",
    response_model=TeamMemberResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_team_member(
    body: TeamMemberCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    member = TeamMember(
        user_id=current_user.id,
        name=body.name,
        role=body.role,
        personality_traits=body.personality_traits,
        communication_preference=body.communication_preference,
        decision_style=body.decision_style,
        reporting_preference=body.reporting_preference,
        taboo=body.taboo,
        notes=body.notes,
    )
    db.add(member)
    db.commit()
    db.refresh(member)
    return member


@router.get("/members/{member_id}", response_model=TeamMemberResponse)
def get_team_member(
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    return _member_for_user(db, current_user.id, member_id)


@router.patch("/members/{member_id}", response_model=TeamMemberResponse)
def update_team_member(
    member_id: uuid.UUID,
    body: TeamMemberUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    member = _member_for_user(db, current_user.id, member_id)
    data = body.model_dump(exclude_unset=True)
    for field, value in data.items():
        setattr(member, field, value)
    member.updated_at = datetime.now(timezone.utc)  # reliable bump for SQLite + stale prompts (6.7)
    db.commit()
    db.refresh(member)
    return member


@router.delete("/members/{member_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_team_member(
    member_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    member = _member_for_user(db, current_user.id, member_id)
    db.delete(member)
    db.commit()
    return None


@router.post(
    "/members/{member_id}/behavior-tags",
    response_model=list[BehaviorTagResponse],
    status_code=status.HTTP_200_OK,
)
def confirm_behavior_tags(
    member_id: uuid.UUID,
    body: ConfirmBehaviorTagsBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Persist user-confirmed behavior tags (after optional edits from finalize draft)."""
    _member_for_user(db, current_user.id, member_id)
    try:
        rows = persist_behavior_tags(
            db,
            user_id=current_user.id,
            member_id=member_id,
            tags=[t.model_dump() for t in body.tags],
            update_mode=body.update_mode,
        )
    except ValueError as e:
        if str(e) == "Team member not found":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=str(e),
            ) from e
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return rows
