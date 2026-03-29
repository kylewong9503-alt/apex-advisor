from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.work import (
    ComplianceTrend,
    DeliverablePattern,
    DeliverableStandard,
    DimensionReview,
    DimensionStatus,
    ReviewHistory,
    WorkProfile,
)
from app.services.ai.base import BaseLLMService, get_llm_service

REVIEW_SYSTEM = (
    "[deliverable_review]\n"
    "You are Deliverable_Review. Compare the user's draft against the five standard "
    "dimensions (format, style, framework, perspective, habit).\n"
    "Respond with JSON only:\n"
    "{\n"
    '  "overall_compliance": <0.0-1.0>,\n'
    '  "dimensions": [\n'
    "    {\n"
    '      "dimension": "format"|"style"|"framework"|"perspective"|"habit",\n'
    '      "status": "compliant"|"non-compliant",\n'
    '      "details": "<brief assessment>",\n'
    '      "suggestions": "<if non-compliant, concrete fix; else null>"\n'
    "    }\n"
    "  ]\n"
    "}\n"
)

PATTERN_SYSTEM = (
    "[deliverable_pattern]\n"
    "Given review history summaries for one work scene, produce a Deliverable_Pattern.\n"
    "Respond with JSON only:\n"
    "{\n"
    '  "persistent_issues": ["<issue>", ...],\n'
    '  "improving_dimensions": ["<dimension label>", ...],\n'
    '  "compliance_trend": "improving"|"declining"|"stable",\n'
    '  "trend_description": "<one paragraph>"\n'
    "}\n"
)


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _dimension_status(s: str) -> DimensionStatus:
    s = (s or "").lower().strip()
    if s == "compliant":
        return DimensionStatus.COMPLIANT
    return DimensionStatus.NON_COMPLIANT


def _trend(s: str) -> ComplianceTrend:
    s = (s or "").lower().strip()
    if s == "declining":
        return ComplianceTrend.DECLINING
    if s == "stable":
        return ComplianceTrend.STABLE
    return ComplianceTrend.IMPROVING


class DeliverableReviewService:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _client(self) -> BaseLLMService:
        return self._llm if self._llm is not None else get_llm_service()

    async def _complete(self, system: str, user: str) -> str:
        return await self._client().generate_response(user, system_message=system)

    def _get_standard(
        self, db: Session, user_id: uuid.UUID, standard_id: uuid.UUID
    ) -> Tuple[DeliverableStandard, WorkProfile]:
        std = (
            db.query(DeliverableStandard)
            .join(WorkProfile)
            .filter(
                DeliverableStandard.id == standard_id,
                WorkProfile.user_id == user_id,
            )
            .first()
        )
        if not std:
            raise ValueError("Deliverable standard not found")
        return std, std.work_profile

    def _standard_prompt_block(self, std: DeliverableStandard) -> str:
        return (
            f"Work_Scene: {std.work_scene}\n"
            f"格式要求: {std.format_requirements or '（未填写）'}\n"
            f"体例要求: {std.style_requirements or '（未填写）'}\n"
            f"思路框架: {std.thinking_framework or '（未填写）'}\n"
            f"立场与视角: {std.perspective_and_stance or '（未填写）'}\n"
            f"习惯偏好: {std.habit_preferences or '（未填写）'}\n"
        )

    def _pattern_context(self, db: Session, standard_id: uuid.UUID) -> str:
        p = (
            db.query(DeliverablePattern)
            .filter(DeliverablePattern.deliverable_standard_id == standard_id)
            .order_by(DeliverablePattern.updated_at.desc())
            .first()
        )
        if not p:
            return "(no prior Deliverable_Pattern)"
        issues = p.persistent_issues or "[]"
        improving = p.improving_dimensions or "[]"
        return (
            f"Prior pattern (for context):\n"
            f"Trend: {p.compliance_trend.value if hasattr(p.compliance_trend, 'value') else p.compliance_trend}\n"
            f"Persistent issues (JSON): {issues}\n"
            f"Improving dimensions (JSON): {improving}\n"
            f"Summary: {p.trend_description}\n"
        )

    async def review_draft(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        deliverable_standard_id: uuid.UUID,
        draft_text: str,
    ) -> Tuple[ReviewHistory, bool]:
        std, _ = self._get_standard(db, user_id, deliverable_standard_id)
        pattern_ctx = self._pattern_context(db, std.id)
        user_prompt = (
            f"{pattern_ctx}\n\nSTANDARD:\n{self._standard_prompt_block(std)}\n\n"
            f"DRAFT:\n{draft_text.strip()}"
        )
        raw = await self._complete(REVIEW_SYSTEM, user_prompt)
        data = _parse_json_object(raw)

        overall = float(data.get("overall_compliance", 0.0))
        overall = max(0.0, min(1.0, overall))

        dims_raw = data.get("dimensions") or []
        if not isinstance(dims_raw, list):
            dims_raw = []

        rh = ReviewHistory(
            deliverable_standard_id=std.id,
            overall_compliance=overall,
            reviewed_at=datetime.now(timezone.utc),
        )
        db.add(rh)
        db.flush()

        seen: set[str] = set()
        for d in dims_raw:
            if not isinstance(d, dict):
                continue
            key = str(d.get("dimension", "")).lower().strip()
            if key not in ("format", "style", "framework", "perspective", "habit"):
                continue
            if key in seen:
                continue
            seen.add(key)
            dr = DimensionReview(
                review_history_id=rh.id,
                dimension=key,
                status=_dimension_status(str(d.get("status", ""))),
                details=str(d.get("details") or ""),
                suggestions=str(d.get("suggestions") or "") or None,
            )
            db.add(dr)

        # Ensure five dimensions present (fill compliant gaps if model omitted)
        for key in ("format", "style", "framework", "perspective", "habit"):
            if key not in seen:
                db.add(
                    DimensionReview(
                        review_history_id=rh.id,
                        dimension=key,
                        status=DimensionStatus.COMPLIANT,
                        details="（模型未返回该维度，默认符合）",
                        suggestions=None,
                    )
                )

        db.flush()

        n = (
            db.query(ReviewHistory)
            .filter(ReviewHistory.deliverable_standard_id == std.id)
            .count()
        )
        recomputed = False
        if n >= 3:
            recomputed = await self._recompute_pattern(db, std.id)

        db.commit()
        db.refresh(rh)
        return rh, recomputed

    async def _recompute_pattern(self, db: Session, deliverable_standard_id: uuid.UUID) -> bool:
        histories = (
            db.query(ReviewHistory)
            .filter(ReviewHistory.deliverable_standard_id == deliverable_standard_id)
            .order_by(ReviewHistory.reviewed_at.desc())
            .all()
        )
        if len(histories) < 3:
            return False

        lines: List[str] = []
        for h in reversed(histories[-10:]):
            parts = [f"t={h.reviewed_at.isoformat()}", f"overall={h.overall_compliance:.2f}"]
            for dr in h.dimension_reviews:
                st = dr.status.value if hasattr(dr.status, "value") else dr.status
                parts.append(f"{dr.dimension}:{st}")
            lines.append(" | ".join(parts))

        blob = "\n".join(lines)
        raw = await self._complete(
            PATTERN_SYSTEM,
            f"Review history lines ({len(histories)} total):\n{blob}",
        )
        data = _parse_json_object(raw)

        issues = data.get("persistent_issues") or []
        improving = data.get("improving_dimensions") or []
        if not isinstance(issues, list):
            issues = []
        if not isinstance(improving, list):
            improving = []

        db.query(DeliverablePattern).filter(
            DeliverablePattern.deliverable_standard_id == deliverable_standard_id
        ).delete(synchronize_session=False)

        pat = DeliverablePattern(
            deliverable_standard_id=deliverable_standard_id,
            based_on_count=len(histories),
            persistent_issues=json.dumps([str(x) for x in issues], ensure_ascii=False),
            improving_dimensions=json.dumps([str(x) for x in improving], ensure_ascii=False),
            compliance_trend=_trend(str(data.get("compliance_trend", "stable"))),
            trend_description=str(data.get("trend_description") or ""),
        )
        db.add(pat)
        db.flush()
        return True


def load_pattern_json(text: Optional[str]) -> List[str]:
    if not text:
        return []
    try:
        v = json.loads(text)
        return [str(x) for x in v] if isinstance(v, list) else []
    except json.JSONDecodeError:
        return []
