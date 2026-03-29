from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.prompt_template import SavedPromptTemplate
from app.models.user import User
from app.schemas.prompt import (
    PromptGenerateRequest,
    PromptGenerateResponse,
    SavedPromptTemplateCreate,
    SavedPromptTemplateResponse,
    SavedPromptTemplateUpdate,
)
from app.services.ai.prompt_engine import (
    PROMPT_SCENES,
    PromptEngine,
    max_profile_updated_at,
    template_needs_refresh,
)

router = APIRouter()
_engine = PromptEngine()


def _template_response(
    db: Session, user_id: uuid.UUID, row: SavedPromptTemplate
) -> SavedPromptTemplateResponse:
    sync = row.last_synced_at or row.created_at
    needs = template_needs_refresh(db, user_id, sync)
    return SavedPromptTemplateResponse(
        id=row.id,
        name=row.name,
        scene=row.scene,
        body=row.body,
        version=row.version,
        needs_refresh=needs,
        last_synced_at=row.last_synced_at,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


@router.get("/scenes", response_model=dict[str, str])
def list_scenes(current_user: User = Depends(get_current_active_user)):
    """Five preset scenes (Requirement 6.2)."""
    return PROMPT_SCENES


@router.post("/generate", response_model=PromptGenerateResponse)
async def generate_prompt(
    body: PromptGenerateRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if body.scene not in PROMPT_SCENES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid scene. Allowed: {list(PROMPT_SCENES.keys())}",
        )
    try:
        text, hints = await _engine.generate(
            db,
            user_id=current_user.id,
            scene=body.scene,
            team_member_id=body.team_member_id,
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        ) from e
    return PromptGenerateResponse(
        scene=body.scene,
        scene_label_zh=PROMPT_SCENES[body.scene],
        template=text,
        placeholder_hints=hints,
    )


@router.get("/templates", response_model=list[SavedPromptTemplateResponse])
def list_saved_templates(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(SavedPromptTemplate)
        .filter(SavedPromptTemplate.user_id == current_user.id)
        .order_by(SavedPromptTemplate.updated_at.desc())
        .all()
    )
    return [_template_response(db, current_user.id, r) for r in rows]


@router.post(
    "/templates",
    response_model=SavedPromptTemplateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_saved_template(
    body: SavedPromptTemplateCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if body.scene not in PROMPT_SCENES:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid scene",
        )
    now = datetime.now(timezone.utc)
    row = SavedPromptTemplate(
        user_id=current_user.id,
        name=body.name.strip(),
        scene=body.scene,
        body=body.body.strip(),
        version=1,
        last_synced_at=now,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return _template_response(db, current_user.id, row)


@router.get("/templates/{template_id}", response_model=SavedPromptTemplateResponse)
def get_saved_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(SavedPromptTemplate)
        .filter(
            SavedPromptTemplate.id == template_id,
            SavedPromptTemplate.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return _template_response(db, current_user.id, row)


@router.patch("/templates/{template_id}", response_model=SavedPromptTemplateResponse)
def update_saved_template(
    template_id: uuid.UUID,
    body: SavedPromptTemplateUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(SavedPromptTemplate)
        .filter(
            SavedPromptTemplate.id == template_id,
            SavedPromptTemplate.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.name is not None:
        row.name = body.name.strip()
    if body.body is not None:
        row.body = body.body.strip()
        row.version = (row.version or 1) + 1
        row.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _template_response(db, current_user.id, row)


@router.delete("/templates/{template_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_saved_template(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(SavedPromptTemplate)
        .filter(
            SavedPromptTemplate.id == template_id,
            SavedPromptTemplate.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


@router.post(
    "/templates/{template_id}/mark-synced",
    response_model=SavedPromptTemplateResponse,
)
def mark_template_synced(
    template_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """User acknowledges profiles are reflected; clears needs_refresh until next profile edit (6.7)."""
    row = (
        db.query(SavedPromptTemplate)
        .filter(
            SavedPromptTemplate.id == template_id,
            SavedPromptTemplate.user_id == current_user.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    row.last_synced_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(row)
    return _template_response(db, current_user.id, row)


@router.get("/templates/stale-check", response_model=dict)
def stale_summary(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Expose max profile revision time for debugging refresh logic."""
    m = max_profile_updated_at(db, current_user.id)
    return {
        "max_profile_updated_at": m.isoformat() if m else None,
    }
