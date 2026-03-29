from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.industry import (
    INDUSTRY_INTELLIGENCE_DIMENSIONS,
    ConflictFlag,
    IndustryDigest,
    IndustryDigestStatus,
    IndustryInput,
    IndustryItem,
    IndustryProfile,
    IndustrySourceType,
    TopicMaterial,
)
from app.services.ai.base import BaseLLMService, get_llm_service

MIN_INDUSTRY_INPUT_LENGTH = 50

CLASSIFIER_SYSTEM = (
    "[industry_classifier]\n"
    "You are Industry_Classifier. Map raw industry materials to four dimensions; use "
    "empty string if nothing applies:\n"
    "- industry_trends\n"
    "- competitive_landscape\n"
    "- regulatory_dynamics\n"
    "- data_highlights\n"
    "Annotate each dimension as new, supplement, or conflict vs existing knowledge.\n"
    "Propose topic_materials for interpersonal conversation (title, core_insight, data_support, tags).\n"
    "For data_highlights, optionally include data_card: {value, source, as_of}.\n"
    "Respond with JSON only:\n"
    "{\n"
    '  "industry_trends": "<text>",\n'
    "  ...,\n"
    '  "dimension_annotations": { "<dim>": "new"|"supplement"|"conflict" },\n'
    '  "conflicts": [{"dimension": "<dim>", "description": "<text>"}],\n'
    '  "suggested_merge": { "<dim>": "supplement"|"override" },\n'
    '  "topic_materials": [{"title":"","core_insight":"","data_support":"","tags":[]}],\n'
    '  "data_card": {"value":"","source":"","as_of":""} | null\n'
    "}\n"
)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _summarize_existing(items: List[IndustryItem]) -> str:
    if not items:
        return "(no existing industry intelligence yet)"
    lines: List[str] = []
    for it in items:
        flag = ""
        if it.conflict_flag:
            fv = it.conflict_flag.value if hasattr(it.conflict_flag, "value") else it.conflict_flag
            flag = f" [{fv}]"
        lines.append(f"[{it.dimension}]{flag} {it.content}")
    return "\n".join(lines[:80])


def _annotation_to_flag(s: Optional[str]) -> Optional[ConflictFlag]:
    if not s:
        return None
    x = str(s).lower().strip()
    if x == "new":
        return ConflictFlag.NEW
    if x == "supplement":
        return ConflictFlag.SUPPLEMENT
    if x == "conflict":
        return ConflictFlag.CONFLICT
    return ConflictFlag.NEW


class IndustryClassifier:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _client(self) -> BaseLLMService:
        return self._llm if self._llm is not None else get_llm_service()

    async def _complete(self, user_prompt: str) -> str:
        return await self._client().generate_response(
            user_prompt,
            system_message=CLASSIFIER_SYSTEM,
        )

    def _profile(self, db: Session, user_id: uuid.UUID) -> IndustryProfile:
        p = db.query(IndustryProfile).filter(IndustryProfile.user_id == user_id).first()
        if not p:
            raise ValueError("Industry profile not found")
        return p

    def validate_length(self, combined: str) -> None:
        if len(combined.strip()) < MIN_INDUSTRY_INPUT_LENGTH:
            raise ValueError(
                f"Industry input is too short; provide at least "
                f"{MIN_INDUSTRY_INPUT_LENGTH} characters of substance."
            )

    async def classify_inputs(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        input_ids: List[uuid.UUID],
    ) -> IndustryDigest:
        profile = self._profile(db, user_id)
        rows = (
            db.query(IndustryInput)
            .filter(
                IndustryInput.industry_profile_id == profile.id,
                IndustryInput.id.in_(input_ids),
            )
            .all()
        )
        if len(rows) != len(set(input_ids)):
            raise ValueError("One or more industry inputs were not found")
        combined = "\n\n".join(r.raw_text.strip() for r in rows)
        self.validate_length(combined)

        existing = (
            db.query(IndustryItem)
            .filter(IndustryItem.industry_profile_id == profile.id)
            .all()
        )
        blob = _summarize_existing(existing)
        user_prompt = f"EXISTING_KNOWLEDGE:\n{blob}\n\nNEW_INPUTS:\n{combined}"
        raw = await self._complete(user_prompt)
        data = _parse_json_object(raw)

        summaries: Dict[str, str] = {}
        for dim in INDUSTRY_INTELLIGENCE_DIMENSIONS:
            v = data.get(dim)
            summaries[dim] = (v or "").strip() if isinstance(v, str) else ""

        suggested = data.get("suggested_merge") or {}
        merge_modes: Dict[str, str] = {}
        for dim in INDUSTRY_INTELLIGENCE_DIMENSIONS:
            m = suggested.get(dim)
            merge_modes[dim] = m if m in ("supplement", "override") else "supplement"

        ann_in = data.get("dimension_annotations") or {}
        annotations: Dict[str, str] = {}
        if isinstance(ann_in, dict):
            for dim in INDUSTRY_INTELLIGENCE_DIMENSIONS:
                v = ann_in.get(dim)
                if isinstance(v, str) and v.lower().strip() in ("new", "supplement", "conflict"):
                    annotations[dim] = v.lower().strip()
                else:
                    annotations[dim] = "new"

        conflicts_raw = data.get("conflicts") or []
        conflicts: List[Dict[str, str]] = []
        if isinstance(conflicts_raw, list):
            for c in conflicts_raw:
                if isinstance(c, dict) and c.get("dimension"):
                    conflicts.append(
                        {
                            "dimension": str(c["dimension"]),
                            "description": str(c.get("description") or ""),
                        }
                    )

        tm_raw = data.get("topic_materials") or []
        topic_preview: List[Dict[str, Any]] = []
        if isinstance(tm_raw, list):
            for t in tm_raw:
                if not isinstance(t, dict):
                    continue
                title = str(t.get("title") or "").strip()
                if not title:
                    continue
                tags = t.get("tags") or []
                if not isinstance(tags, list):
                    tags = []
                topic_preview.append(
                    {
                        "title": title,
                        "core_insight": str(t.get("core_insight") or ""),
                        "data_support": str(t.get("data_support") or ""),
                        "tags": [str(x) for x in tags if x is not None],
                    }
                )

        data_card = data.get("data_card")
        data_cards: Dict[str, Any] = {}
        if isinstance(data_card, dict) and data_card:
            data_cards["data_highlights"] = {
                "value": str(data_card.get("value") or ""),
                "source": str(data_card.get("source") or ""),
                "as_of": str(data_card.get("as_of") or ""),
            }

        db.query(IndustryDigest).filter(
            IndustryDigest.industry_profile_id == profile.id,
            IndustryDigest.status == IndustryDigestStatus.PENDING_REVIEW,
        ).update({IndustryDigest.status: IndustryDigestStatus.SUPERSEDED}, synchronize_session=False)

        digest = IndustryDigest(
            industry_profile_id=profile.id,
            status=IndustryDigestStatus.PENDING_REVIEW,
            dimension_summaries=summaries,
            dimension_merge_modes=merge_modes,
            dimension_annotations=annotations,
            conflicts=conflicts,
            source_input_ids=[str(x) for x in input_ids],
            topic_materials_preview=topic_preview,
            data_cards=data_cards,
        )
        db.add(digest)
        db.commit()
        db.refresh(digest)
        return digest


def _primary_source(inputs: List[IndustryInput]) -> IndustrySourceType:
    if not inputs:
        return IndustrySourceType.OTHER
    return inputs[0].source_type


def confirm_industry_digest(
    db: Session,
    *,
    user_id: uuid.UUID,
    digest_id: uuid.UUID,
    overrides: Optional[Dict[str, str]] = None,
    merge_overrides: Optional[Dict[str, str]] = None,
) -> tuple[List[IndustryItem], List[TopicMaterial]]:
    profile = db.query(IndustryProfile).filter(IndustryProfile.user_id == user_id).first()
    if not profile:
        raise ValueError("Industry profile not found")

    digest = (
        db.query(IndustryDigest)
        .filter(
            IndustryDigest.id == digest_id,
            IndustryDigest.industry_profile_id == profile.id,
        )
        .first()
    )
    if not digest:
        raise ValueError("Digest not found")
    if digest.status != IndustryDigestStatus.PENDING_REVIEW:
        raise ValueError("Digest is not pending confirmation")

    summaries = dict(digest.dimension_summaries or {})
    if overrides:
        for k, v in overrides.items():
            if k in INDUSTRY_INTELLIGENCE_DIMENSIONS:
                summaries[k] = v

    modes = dict(digest.dimension_merge_modes or {})
    if merge_overrides:
        for k, v in merge_overrides.items():
            if k in INDUSTRY_INTELLIGENCE_DIMENSIONS and v in ("supplement", "override"):
                modes[k] = v

    annotations = dict(digest.dimension_annotations or {})
    data_cards = dict(digest.data_cards or {})

    input_ids = [uuid.UUID(x) for x in (digest.source_input_ids or [])]
    input_rows: List[IndustryInput] = []
    if input_ids:
        input_rows = (
            db.query(IndustryInput)
            .filter(
                IndustryInput.industry_profile_id == profile.id,
                IndustryInput.id.in_(input_ids),
            )
            .all()
        )
    st = _primary_source(input_rows)
    primary_iid = input_rows[0].id if input_rows else None

    highlight_until = datetime.now(timezone.utc) + timedelta(hours=24)
    created_items: List[IndustryItem] = []

    for dim in INDUSTRY_INTELLIGENCE_DIMENSIONS:
        text = (summaries.get(dim) or "").strip()
        if not text:
            continue
        mode = modes.get(dim) or "supplement"
        if mode == "override":
            db.query(IndustryItem).filter(
                IndustryItem.industry_profile_id == profile.id,
                IndustryItem.dimension == dim,
            ).delete(synchronize_session=False)

        cf = _annotation_to_flag(annotations.get(dim))
        dc = None
        if dim == "data_highlights" and isinstance(data_cards.get("data_highlights"), dict):
            dc = data_cards["data_highlights"]

        row = IndustryItem(
            industry_profile_id=profile.id,
            dimension=dim,
            content=text,
            source_type=st,
            conflict_flag=cf,
            industry_input_id=primary_iid,
            data_card=dc,
            highlight_until=highlight_until,
        )
        db.add(row)
        created_items.append(row)

    created_topics: List[TopicMaterial] = []
    for t in digest.topic_materials_preview or []:
        if not isinstance(t, dict):
            continue
        title = str(t.get("title") or "").strip()
        if not title:
            continue
        tags = t.get("tags") or []
        if not isinstance(tags, list):
            tags = []
        tm = TopicMaterial(
            industry_profile_id=profile.id,
            title=title,
            core_insight=str(t.get("core_insight") or ""),
            data_support=str(t.get("data_support") or ""),
            tags=[str(x) for x in tags if x is not None],
        )
        db.add(tm)
        created_topics.append(tm)

    digest.status = IndustryDigestStatus.MERGED
    db.commit()
    for r in created_items:
        db.refresh(r)
    for t in created_topics:
        db.refresh(t)
    return created_items, created_topics
