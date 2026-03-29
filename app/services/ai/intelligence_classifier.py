from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.models.company import (
    COMPANY_INTELLIGENCE_DIMENSIONS,
    CompanyProfile,
    DigestStatus,
    IntelligenceDigest,
    IntelligenceInput,
    IntelligenceItem,
    SourceType,
)
from app.services.ai.base import BaseLLMService, get_llm_service

MIN_INTELLIGENCE_INPUT_LENGTH = 50

CLASSIFIER_SYSTEM = (
    "[intelligence_classifier]\n"
    "You are Intelligence_Classifier. Given raw company notes and optional existing "
    "knowledge, produce structured insights for four dimensions (use empty string if "
    "nothing applies):\n"
    "- business_performance\n"
    "- strategic_direction\n"
    "- cultural_signals\n"
    "- internal_rules\n"
    "Flag conflicts with existing knowledge when applicable.\n"
    "Respond with JSON only, no markdown:\n"
    "{\n"
    '  "business_performance": "<text>",\n'
    '  "strategic_direction": "<text>",\n'
    '  "cultural_signals": "<text>",\n'
    '  "internal_rules": "<text>",\n'
    '  "conflicts": [{"dimension": "<key>", "description": "<text>"}],\n'
    '  "suggested_merge": {\n'
    '    "business_performance": "supplement"|"override",\n'
    "    ...\n"
    "  }\n"
    "}\n"
)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _summarize_existing_items(items: List[IntelligenceItem]) -> str:
    if not items:
        return "(no existing intelligence yet)"
    lines: List[str] = []
    for it in items:
        lines.append(f"[{it.dimension}] {it.content}")
    return "\n".join(lines[:80])


class IntelligenceClassifier:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _client(self) -> BaseLLMService:
        if self._llm is not None:
            return self._llm
        return get_llm_service()

    async def _complete(self, user_prompt: str) -> str:
        return await self._client().generate_response(
            user_prompt,
            system_message=CLASSIFIER_SYSTEM,
        )

    def _get_profile(self, db: Session, user_id: uuid.UUID) -> CompanyProfile:
        profile = db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).first()
        if not profile:
            raise ValueError("Company profile not found")
        return profile

    def validate_inputs_length(self, combined_text: str) -> None:
        if len(combined_text.strip()) < MIN_INTELLIGENCE_INPUT_LENGTH:
            raise ValueError(
                f"Intelligence input is too short; provide at least "
                f"{MIN_INTELLIGENCE_INPUT_LENGTH} characters of substance."
            )

    async def classify_inputs(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        input_ids: List[uuid.UUID],
    ) -> IntelligenceDigest:
        profile = self._get_profile(db, user_id)
        rows = (
            db.query(IntelligenceInput)
            .filter(
                IntelligenceInput.company_profile_id == profile.id,
                IntelligenceInput.id.in_(input_ids),
            )
            .all()
        )
        if len(rows) != len(set(input_ids)):
            raise ValueError("One or more intelligence inputs were not found")
        combined = "\n\n".join(r.raw_text.strip() for r in rows)
        self.validate_inputs_length(combined)

        existing = (
            db.query(IntelligenceItem)
            .filter(IntelligenceItem.company_profile_id == profile.id)
            .all()
        )
        existing_blob = _summarize_existing_items(existing)

        user_prompt = (
            f"EXISTING_KNOWLEDGE:\n{existing_blob}\n\n"
            f"NEW_INPUTS ({len(rows)} fragment(s)):\n{combined}"
        )

        raw = await self._complete(user_prompt)
        data = _parse_json_object(raw)

        summaries: Dict[str, str] = {}
        for dim in COMPANY_INTELLIGENCE_DIMENSIONS:
            v = data.get(dim)
            summaries[dim] = (v or "").strip() if isinstance(v, str) else ""

        suggested = data.get("suggested_merge") or {}
        merge_modes: Dict[str, str] = {}
        for dim in COMPANY_INTELLIGENCE_DIMENSIONS:
            m = suggested.get(dim)
            merge_modes[dim] = m if m in ("supplement", "override") else "supplement"

        conflicts_raw = data.get("conflicts") or []
        conflicts: List[Dict[str, str]] = []
        if isinstance(conflicts_raw, list):
            for c in conflicts_raw:
                if isinstance(c, dict) and c.get("dimension") and c.get("description"):
                    conflicts.append(
                        {
                            "dimension": str(c["dimension"]),
                            "description": str(c["description"]),
                        }
                    )

        # Supersede any older pending digest
        db.query(IntelligenceDigest).filter(
            IntelligenceDigest.company_profile_id == profile.id,
            IntelligenceDigest.status == DigestStatus.PENDING_REVIEW,
        ).update(
            {IntelligenceDigest.status: DigestStatus.SUPERSEDED},
            synchronize_session=False,
        )

        digest = IntelligenceDigest(
            company_profile_id=profile.id,
            status=DigestStatus.PENDING_REVIEW,
            dimension_summaries=summaries,
            dimension_merge_modes=merge_modes,
            conflicts=conflicts,
            source_input_ids=[str(x) for x in input_ids],
        )
        db.add(digest)
        db.commit()
        db.refresh(digest)
        return digest


def _primary_source_type(inputs: List[IntelligenceInput]) -> SourceType:
    if not inputs:
        return SourceType.OTHER
    return inputs[0].source_type


def confirm_digest_merge(
    db: Session,
    *,
    user_id: uuid.UUID,
    digest_id: uuid.UUID,
    overrides: Optional[Dict[str, str]] = None,
    merge_overrides: Optional[Dict[str, str]] = None,
) -> List[IntelligenceItem]:
    """Apply digest into IntelligenceItem rows; 24h highlight on new/updated insights."""
    profile = (
        db.query(CompanyProfile).filter(CompanyProfile.user_id == user_id).first()
    )
    if not profile:
        raise ValueError("Company profile not found")

    digest = (
        db.query(IntelligenceDigest)
        .filter(
            IntelligenceDigest.id == digest_id,
            IntelligenceDigest.company_profile_id == profile.id,
        )
        .first()
    )
    if not digest:
        raise ValueError("Digest not found")
    if digest.status != DigestStatus.PENDING_REVIEW:
        raise ValueError("Digest is not pending confirmation")

    summaries = dict(digest.dimension_summaries or {})
    if overrides:
        for k, v in overrides.items():
            if k in COMPANY_INTELLIGENCE_DIMENSIONS:
                summaries[k] = v

    modes = dict(digest.dimension_merge_modes or {})
    if merge_overrides:
        for k, v in merge_overrides.items():
            if k in COMPANY_INTELLIGENCE_DIMENSIONS and v in ("supplement", "override"):
                modes[k] = v

    input_ids = [uuid.UUID(x) for x in (digest.source_input_ids or [])]
    input_rows: List[IntelligenceInput] = []
    if input_ids:
        input_rows = (
            db.query(IntelligenceInput)
            .filter(
                IntelligenceInput.company_profile_id == profile.id,
                IntelligenceInput.id.in_(input_ids),
            )
            .all()
        )
    source_type = _primary_source_type(input_rows)
    primary_input_id = input_rows[0].id if input_rows else None

    highlight_until = datetime.now(timezone.utc) + timedelta(hours=24)
    created: List[IntelligenceItem] = []

    for dim in COMPANY_INTELLIGENCE_DIMENSIONS:
        text = (summaries.get(dim) or "").strip()
        if not text:
            continue
        mode = modes.get(dim) or "supplement"
        if mode == "override":
            db.query(IntelligenceItem).filter(
                IntelligenceItem.company_profile_id == profile.id,
                IntelligenceItem.dimension == dim,
            ).delete(synchronize_session=False)

        row = IntelligenceItem(
            company_profile_id=profile.id,
            dimension=dim,
            content=text,
            source_type=source_type,
            intelligence_input_id=primary_input_id,
            highlight_until=highlight_until,
        )
        db.add(row)
        created.append(row)

    digest.status = DigestStatus.MERGED
    db.commit()
    for r in created:
        db.refresh(r)
    return created
