from __future__ import annotations

from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.nudge import NudgeDismissal
from app.models.user import User
from app.schemas.nudge import NudgeDismissBody, NudgeItem, NudgesResponse
from app.services.nudge_service import compute_nudges

router = APIRouter()


@router.get("", response_model=NudgesResponse)
def list_nudges(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Banner + card notifications derived from profiles, patterns, and reminders."""
    data = compute_nudges(db, current_user.id)
    return NudgesResponse(
        banners=[NudgeItem(**x) for x in data["banners"]],
        cards=[NudgeItem(**x) for x in data["cards"]],
    )


@router.post("/dismiss", status_code=status.HTTP_204_NO_CONTENT)
def dismiss_nudge(
    body: NudgeDismissBody,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Hide a nudge until underlying data changes (new id) or logic resets."""
    key = body.nudge_key.strip()
    existing = (
        db.query(NudgeDismissal)
        .filter(
            NudgeDismissal.user_id == current_user.id,
            NudgeDismissal.nudge_key == key,
        )
        .first()
    )
    if not existing:
        db.add(
            NudgeDismissal(
                user_id=current_user.id,
                nudge_key=key,
            )
        )
        db.commit()
    return None
