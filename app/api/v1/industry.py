from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.industry import (
    INDUSTRY_INTELLIGENCE_DIMENSIONS,
    IndustryDigest,
    IndustryDigestStatus,
    IndustryInput,
    IndustryItem,
    IndustryProfile,
    IndustryUpdateReminder,
    TopicMaterial,
)
from app.models.user import User
from app.schemas.industry import (
    ClassifyRequest,
    ConfirmIndustryDigestResult,
    DigestConfirmBody,
    DimensionConflict,
    IndustryDigestResponse,
    IndustryInputBatchCreate,
    IndustryInputResponse,
    IndustryItemResponse,
    IndustryItemUpdate,
    IndustryKnowledgeBaseResponse,
    ReminderCreate,
    ReminderResponse,
    ReminderUpdate,
    TopicMaterialCreate,
    TopicMaterialResponse,
    TopicMaterialUpdate,
)
from app.services.ai.industry_classifier import (
    IndustryClassifier,
    MIN_INDUSTRY_INPUT_LENGTH,
    confirm_industry_digest,
)

router = APIRouter()
_classifier = IndustryClassifier()


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _digest_response(d: IndustryDigest) -> IndustryDigestResponse:
    confs = d.conflicts or []
    parsed: list[DimensionConflict] = []
    for c in confs:
        if isinstance(c, dict) and c.get("dimension") is not None:
            parsed.append(
                DimensionConflict(
                    dimension=str(c["dimension"]),
                    description=str(c.get("description") or ""),
                )
            )
    return IndustryDigestResponse(
        id=d.id,
        status=d.status,
        dimension_summaries=dict(d.dimension_summaries or {}),
        dimension_merge_modes=dict(d.dimension_merge_modes or {}),
        dimension_annotations=dict(d.dimension_annotations or {}),
        conflicts=parsed,
        source_input_ids=list(d.source_input_ids or []),
        topic_materials_preview=list(d.topic_materials_preview or []),
        data_cards=dict(d.data_cards or {}),
        created_at=d.created_at,
        updated_at=d.updated_at,
    )


def _item_response(it: IndustryItem) -> IndustryItemResponse:
    return IndustryItemResponse.model_validate(it)


def _next_due(freq: str) -> datetime:
    now = datetime.now(timezone.utc)
    if freq == "weekly":
        return now + timedelta(weeks=1)
    if freq == "biweekly":
        return now + timedelta(weeks=2)
    if freq == "monthly":
        return now + timedelta(days=30)
    return now + timedelta(weeks=1)


@router.get("/knowledge-base", response_model=IndustryKnowledgeBaseResponse)
def get_knowledge_base(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    ip = current_user.industry_profile
    items = (
        db.query(IndustryItem)
        .filter(IndustryItem.industry_profile_id == ip.id)
        .order_by(IndustryItem.updated_at.desc())
        .all()
    )
    by_dim: dict[str, list[IndustryItemResponse]] = {d: [] for d in INDUSTRY_INTELLIGENCE_DIMENSIONS}
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
        db.query(IndustryDigest)
        .filter(
            IndustryDigest.industry_profile_id == ip.id,
            IndustryDigest.status == IndustryDigestStatus.PENDING_REVIEW,
        )
        .order_by(IndustryDigest.created_at.desc())
        .first()
    )

    reminders = (
        db.query(IndustryUpdateReminder)
        .filter(IndustryUpdateReminder.industry_profile_id == ip.id)
        .order_by(IndustryUpdateReminder.dimension.asc())
        .all()
    )

    return IndustryKnowledgeBaseResponse(
        industry_profile_id=ip.id,
        intelligence_by_dimension=by_dim,
        pending_digest=_digest_response(pending) if pending else None,
        insights_banner=banner,
        topic_materials=[TopicMaterialResponse.model_validate(t) for t in ip.topic_materials],
        update_reminders=[ReminderResponse.model_validate(r) for r in reminders],
    )


@router.post(
    "/industry-inputs",
    response_model=list[IndustryInputResponse],
    status_code=status.HTTP_201_CREATED,
)
def create_industry_inputs(
    body: IndustryInputBatchCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    ip = current_user.industry_profile
    out: list[IndustryInput] = []
    for it in body.items:
        row = IndustryInput(
            industry_profile_id=ip.id,
            raw_text=it.raw_text.strip(),
            source_type=it.source_type,
        )
        db.add(row)
        out.append(row)
    db.commit()
    for r in out:
        db.refresh(r)
    return out


@router.post("/industry/classify", response_model=IndustryDigestResponse)
async def classify_industry(
    body: ClassifyRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    try:
        digest = await _classifier.classify_inputs(
            db, user_id=current_user.id, input_ids=body.input_ids
        )
    except ValueError as e:
        msg = str(e)
        if "not found" in msg.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=msg) from e
        if "too short" in msg.lower() or str(MIN_INDUSTRY_INPUT_LENGTH) in msg:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=msg) from e
    return _digest_response(digest)


@router.post(
    "/industry/digests/{digest_id}/confirm",
    response_model=ConfirmIndustryDigestResult,
)
def confirm_digest(
    digest_id: uuid.UUID,
    body: DigestConfirmBody | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    b = body or DigestConfirmBody()
    try:
        items, topics = confirm_industry_digest(
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
    return ConfirmIndustryDigestResult(
        industry_items=[_item_response(i) for i in items],
        topic_materials=[TopicMaterialResponse.model_validate(t) for t in topics],
    )


@router.patch("/intelligence/items/{item_id}", response_model=IndustryItemResponse)
def update_industry_item(
    item_id: uuid.UUID,
    body: IndustryItemUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    it = (
        db.query(IndustryItem)
        .filter(
            IndustryItem.id == item_id,
            IndustryItem.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not it:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    it.content = body.content.strip()
    db.commit()
    db.refresh(it)
    return _item_response(it)


@router.delete("/intelligence/items/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_industry_item(
    item_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    it = (
        db.query(IndustryItem)
        .filter(
            IndustryItem.id == item_id,
            IndustryItem.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not it:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(it)
    db.commit()
    return None


@router.get("/topic-materials", response_model=list[TopicMaterialResponse])
def list_topic_materials(
    tag: str | None = Query(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    q = db.query(TopicMaterial).filter(
        TopicMaterial.industry_profile_id == current_user.industry_profile.id
    )
    rows = q.order_by(TopicMaterial.updated_at.desc()).all()
    if tag and tag.strip():
        t = tag.strip().lower()
        rows = [r for r in rows if t in [x.lower() for x in (r.tags or [])]]
    return [TopicMaterialResponse.model_validate(r) for r in rows]


@router.post(
    "/topic-materials",
    response_model=TopicMaterialResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_topic_material(
    body: TopicMaterialCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    row = TopicMaterial(
        industry_profile_id=current_user.industry_profile.id,
        title=body.title.strip(),
        core_insight=body.core_insight,
        data_support=body.data_support,
        tags=body.tags or [],
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.patch("/topic-materials/{tm_id}", response_model=TopicMaterialResponse)
def update_topic_material(
    tm_id: uuid.UUID,
    body: TopicMaterialUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    row = (
        db.query(TopicMaterial)
        .filter(
            TopicMaterial.id == tm_id,
            TopicMaterial.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    data = body.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(row, k, v)
    db.commit()
    db.refresh(row)
    return row


@router.delete("/topic-materials/{tm_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_topic_material(
    tm_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    row = (
        db.query(TopicMaterial)
        .filter(
            TopicMaterial.id == tm_id,
            TopicMaterial.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None


@router.post(
    "/update-reminders",
    response_model=ReminderResponse,
    status_code=status.HTTP_201_CREATED,
)
def upsert_reminder(
    body: ReminderCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    dim = body.dimension.strip()
    if dim not in INDUSTRY_INTELLIGENCE_DIMENSIONS:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Invalid dimension: {dim}",
        )
    ip = current_user.industry_profile
    existing = (
        db.query(IndustryUpdateReminder)
        .filter(
            IndustryUpdateReminder.industry_profile_id == ip.id,
            IndustryUpdateReminder.dimension == dim,
        )
        .first()
    )
    freq_val = body.frequency.value if hasattr(body.frequency, "value") else str(body.frequency)
    nd = _next_due(freq_val)
    if existing:
        existing.frequency = body.frequency
        existing.next_due_at = nd
        db.commit()
        db.refresh(existing)
        return existing
    row = IndustryUpdateReminder(
        industry_profile_id=ip.id,
        dimension=dim,
        frequency=body.frequency,
        next_due_at=nd,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


@router.get("/update-reminders", response_model=list[ReminderResponse])
def list_reminders(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    rows = (
        db.query(IndustryUpdateReminder)
        .filter(IndustryUpdateReminder.industry_profile_id == current_user.industry_profile.id)
        .order_by(IndustryUpdateReminder.dimension.asc())
        .all()
    )
    return rows


@router.patch("/update-reminders/{reminder_id}", response_model=ReminderResponse)
def patch_reminder(
    reminder_id: uuid.UUID,
    body: ReminderUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    row = (
        db.query(IndustryUpdateReminder)
        .filter(
            IndustryUpdateReminder.id == reminder_id,
            IndustryUpdateReminder.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    if body.frequency is not None:
        row.frequency = body.frequency
        fv = body.frequency.value if hasattr(body.frequency, "value") else str(body.frequency)
        row.next_due_at = _next_due(fv)
    if body.next_due_at is not None:
        row.next_due_at = body.next_due_at
    db.commit()
    db.refresh(row)
    return row


@router.delete("/update-reminders/{reminder_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_reminder(
    reminder_id: uuid.UUID,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    if not current_user.industry_profile:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Industry profile not found")
    row = (
        db.query(IndustryUpdateReminder)
        .filter(
            IndustryUpdateReminder.id == reminder_id,
            IndustryUpdateReminder.industry_profile_id == current_user.industry_profile.id,
        )
        .first()
    )
    if not row:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    db.delete(row)
    db.commit()
    return None
