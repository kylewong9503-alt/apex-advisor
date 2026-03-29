from __future__ import annotations

import uuid
from datetime import datetime, timezone


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.company import (
    COMPANY_INTELLIGENCE_DIMENSIONS,
    DigestStatus,
    IntelligenceDigest,
    IntelligenceInput,
    IntelligenceItem,
    StakeholderNote,
)
from app.models.user import User
from app.schemas.company import (
    ClassifyRequest,
    CompanyKnowledgeBaseResponse,
    DigestConfirmBody,
    DimensionConflict,
    IntelligenceDigestResponse,
    IntelligenceInputBatchCreate,
    IntelligenceInputResponse,
    IntelligenceItemResponse,
    IntelligenceItemUpdate,
    StakeholderCreate,
    StakeholderResponse,
    StakeholderUpdate,
)
from app.services.ai.intelligence_classifier import (
    IntelligenceClassifier,
    MIN_INTELLIGENCE_INPUT_LENGTH,
    confirm_digest_merge,
)

router = APIRouter()
_classifier = IntelligenceClassifier()


def _digest_response(d: IntelligenceDigest) -> IntelligenceDigestResponse:
    confs = d.conflicts or []
    parsed = []
    for c in confs:
        if isinstance(c, dict) and c.get("dimension") is not None:
            parsed.append(
                DimensionConflict(
                    dimension=str(c["dimension"]),
                    description=str(c.get("description") or ""),
                )
            )
    return IntelligenceDigestResponse(
        id=d.id,
        status=d.status,
        dimension_summaries=dict(d.dimension_summaries or {}),
        dimension_merge_modes=dict(d.dimension_merge_modes or {}),
        conflicts=parsed,
        source_input_ids=list(d.source_input_ids or []),
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _item_response(it: IntelligenceItem) -> IntelligenceItemResponse:
    return IntelligenceItemResponse.model_validate(it)


@router.get("/knowledge-base", response_model=CompanyKnowledgeBaseResponse)
def get_knowledge_base(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company profile not found",
        )
    cp = current_user.company_profile
    items = (
        db.query(IntelligenceItem)
        .filter(IntelligenceItem.company_profile_id == cp.id)
        .order_by(IntelligenceItem.updated_at.desc())
        .all()
    )
    by_dim: dict[str, list[IntelligenceItemResponse]] = {d: [] for d in COMPANY_INTELLIGENCE_DIMENSIONS}
    for it in items:
        if it.dimension in by_dim:
            by_dim[it.dimension].append(_item_response(it))

    now = datetime.now(timezone.utc)
    banner = [
        _item_response(it)
        for it in items
        if it.highlight_until and _utc(it.highlight_until) > now
    ]

    pending = (
        db.query(IntelligenceDigest)
        .filter(
            IntelligenceDigest.company_profile_id == cp.id,
            IntelligenceDigest.status == DigestStatus.PENDING_REVIEW,
        )
        .order_by(IntelligenceDigest.created_at.desc())
        .first()
    )

    stakeholders = (
        db.query(StakeholderNote)
        .filter(StakeholderNote.company_profile_id == cp.id)
        .order_by(StakeholderNote.created_at.asc())
        .all()
    )

    return CompanyKnowledgeBaseResponse(
        company_profile_id=cp.id,
        intelligence_by_dimension=by_dim,
        pending_digest=_digest_response(pending) if pending else None,
        insights_banner=banner,
        stakeholders=[StakeholderResponse.model_validate(s) for s in stakeholders],
    )


@router.post(
    "/intelligence-inputs",
    response_model=list[IntelligenceInputResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_intelligence_inputs(
    body: IntelligenceInputBatchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company profile not found",
        )
    cp = current_user.company_profile
    out: list[IntelligenceInput] = []
    for it in body.items:
        row = IntelligenceInput(
            company_profile_id=cp.id,
            raw_text=it.raw_text.strip(),
            source_type=it.source_type,
        )
        db.add(row)
        out.append(row)
    db.commit()
    for r in out:
        db.refresh(r)
    return out


@router.post("/intelligence/classify", response_model=IntelligenceDigestResponse)
async def classify_intelligence(
    body: ClassifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Company profile not found",
        )
    try:
        digest = await _classifier.classify_inputs(
            db, user_id=current_user.id, input_ids=body.input_ids
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from e
        if "too short" in msg.lower() or str(MIN_INTELLIGENCE_INPUT_LENGTH) in msg:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=msg,
            ) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
    return _digest_response(digest)


@router.post(
    "/intelligence/digests/{digest_id}/confirm",
    response_model=list[IntelligenceItemResponse],
)
def confirm_intelligence_digest(
    digest_id: uuid.UUID,
    body: DigestConfirmBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    b = body or DigestConfirmBody()
    try:
        rows = confirm_digest_merge(
            db,
            user_id=current_user.id,
            digest_id=digest_id,
            overrides=b.dimension_summaries,
            merge_overrides=b.dimension_merge_modes,
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
    return [_item_response(r) for r in rows]


@router.patch(
    "/intelligence/items/{item_id}",
    response_model=IntelligenceItemResponse,
)
def update_intelligence_item(
    item_id: uuid.UUID,
    body: IntelligenceItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    it = (
        db.query(IntelligenceItem)
        .filter(
            IntelligenceItem.id == item_id,
            IntelligenceItem.company_profile_id == current_user.company_profile.id,
        )
        .first()
    )
    if not it:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intelligence item not found")
    it.content = body.content.strip()
    db.commit()
    db.refresh(it)
    return _item_response(it)


@router.delete("/intelligence/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_intelligence_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    it = (
        db.query(IntelligenceItem)
        .filter(
            IntelligenceItem.id == item_id,
            IntelligenceItem.company_profile_id == current_user.company_profile.id,
        )
        .first()
    )
    if not it:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Intelligence item not found")
    db.delete(it)
    db.commit()
    return None


@router.post(
    "/stakeholders",
    response_model=StakeholderResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_stakeholder(
    body: StakeholderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    n = StakeholderNote(
        company_profile_id=current_user.company_profile.id,
        name=body.name.strip(),
        department=body.department.strip() if body.department else None,
        influence_description=body.influence_description,
    )
    db.add(n)
    db.commit()
    db.refresh(n)
    return StakeholderResponse.model_validate(n)


@router.get("/stakeholders", response_model=list[StakeholderResponse])
def list_stakeholders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    rows = (
        db.query(StakeholderNote)
        .filter(StakeholderNote.company_profile_id == current_user.company_profile.id)
        .order_by(StakeholderNote.created_at.asc())
        .all()
    )
    return [StakeholderResponse.model_validate(r) for r in rows]


@router.patch("/stakeholders/{note_id}", response_model=StakeholderResponse)
def update_stakeholder(
    note_id: uuid.UUID,
    body: StakeholderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    n = (
        db.query(StakeholderNote)
        .filter(
            StakeholderNote.id == note_id,
            StakeholderNote.company_profile_id == current_user.company_profile.id,
        )
        .first()
    )
    if not n:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stakeholder not found")
    data = body.model_dump(exclude_unset=True)
    if "name" in data and data["name"] is not None:
        n.name = data["name"].strip()
    if "department" in data:
        n.department = data["department"].strip() if data["department"] else None
    if "influence_description" in data:
        n.influence_description = data["influence_description"]
    db.commit()
    db.refresh(n)
    return StakeholderResponse.model_validate(n)


@router.delete("/stakeholders/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_stakeholder(
    note_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.company_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company profile not found")
    n = (
        db.query(StakeholderNote)
        .filter(
            StakeholderNote.id == note_id,
            StakeholderNote.company_profile_id == current_user.company_profile.id,
        )
        .first()
    )
    if not n:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Stakeholder not found")
    db.delete(n)
    db.commit()
    return None
