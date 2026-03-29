"""
Contextual nudges: time-based profile reminders, deliverable review patterns,
industry update reminders (Task 11.1).
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Set

from sqlalchemy.orm import Session

from app.models.nudge import NudgeDismissal
from app.models.work import ComplianceTrend, DeliverablePattern, ReviewHistory, WorkProfile
from app.services.ai.prompt_engine import max_profile_updated_at

STALE_PROFILE_DAYS = 14
REVIEW_IDLE_DAYS = 30
REMINDER_SOON_DAYS = 3


def _utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _dismissed_keys(db: Session, user_id: uuid.UUID) -> Set[str]:
    rows = db.query(NudgeDismissal.nudge_key).filter(NudgeDismissal.user_id == user_id).all()
    return {r[0] for r in rows}


def compute_nudges(db: Session, user_id: uuid.UUID) -> Dict[str, List[Dict[str, Any]]]:
    """Return { 'banners': [...], 'cards': [...] } as plain dicts for Pydantic."""
    now = datetime.now(timezone.utc)
    dismissed = _dismissed_keys(db, user_id)
    banners: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []

    # --- Time-based: overall profile freshness ---
    max_ts = max_profile_updated_at(db, user_id)
    if max_ts and f"profile_stale_{STALE_PROFILE_DAYS}d" not in dismissed:
        age = now - _utc(max_ts)
        if age > timedelta(days=STALE_PROFILE_DAYS):
            banners.append(
                {
                    "id": f"profile_stale_{STALE_PROFILE_DAYS}d",
                    "kind": "banner",
                    "severity": "warning",
                    "title": "档案更新提醒",
                    "body": f"距离最近一次档案变更已超过 {STALE_PROFILE_DAYS} 天，建议核对团队 / 公司 / 工作 / 行业信息是否仍准确。",
                    "action_label": "打开工作档案",
                    "action_href": "/ui/work",
                }
            )

    # --- Industry scheduled reminders ---
    from app.models.industry import IndustryProfile

    ind = db.query(IndustryProfile).filter(IndustryProfile.user_id == user_id).first()
    if ind:
        for rem in ind.update_reminders or []:
            nk = f"industry_reminder_{rem.id}"
            if nk in dismissed:
                continue
            due = _utc(rem.next_due_at) if rem.next_due_at else None
            if due is None:
                continue
            if due <= now + timedelta(days=REMINDER_SOON_DAYS):
                banners.append(
                    {
                        "id": nk,
                        "kind": "banner",
                        "severity": "info",
                        "title": "行业情报更新窗口",
                        "body": f"维度「{rem.dimension}」按 {rem.frequency.value if hasattr(rem.frequency, 'value') else rem.frequency} 节奏即将到期，可补充最新输入。",
                        "action_label": "行业情报",
                        "action_href": "/ui/industry",
                    }
                )

    # --- Deliverable pattern & review cadence ---
    wp = db.query(WorkProfile).filter(WorkProfile.user_id == user_id).first()
    if wp:
        for std in wp.deliverable_standards or []:
            sid = str(std.id)
            # Latest pattern
            pat = (
                db.query(DeliverablePattern)
                .filter(DeliverablePattern.deliverable_standard_id == std.id)
                .order_by(DeliverablePattern.updated_at.desc())
                .first()
            )
            if pat:
                nk_pat = f"deliverable_pattern_{sid}"
                if nk_pat not in dismissed:
                    trend = pat.compliance_trend
                    trend_val = trend.value if hasattr(trend, "value") else str(trend)
                    if trend == ComplianceTrend.DECLINING or trend_val == "declining":
                        cards.append(
                            {
                                "id": nk_pat,
                                "kind": "card",
                                "severity": "warning",
                                "title": f"交付评审趋势：{std.work_scene}",
                                "body": pat.trend_description[:500]
                                if pat.trend_description
                                else "合规趋势下滑，建议安排新一轮草稿评审。",
                                "action_label": "工作档案",
                                "action_href": "/ui/work",
                            }
                        )
                    else:
                        try:
                            issues = json.loads(pat.persistent_issues or "[]")
                        except json.JSONDecodeError:
                            issues = []
                        if issues and f"deliverable_issues_{sid}" not in dismissed:
                            cards.append(
                                {
                                    "id": f"deliverable_issues_{sid}",
                                    "kind": "card",
                                    "severity": "tip",
                                    "title": f"反复出现的问题：{std.work_scene}",
                                    "body": "；".join(str(x) for x in issues[:5]),
                                    "action_label": "去评审",
                                    "action_href": "/ui/work",
                                }
                            )

            # Review idle
            last_rh = (
                db.query(ReviewHistory)
                .filter(ReviewHistory.deliverable_standard_id == std.id)
                .order_by(ReviewHistory.reviewed_at.desc())
                .first()
            )
            nk_idle = f"review_idle_{sid}"
            if nk_idle not in dismissed and last_rh:
                reviewed = _utc(last_rh.reviewed_at)
                if reviewed and (now - reviewed) > timedelta(days=REVIEW_IDLE_DAYS):
                    cards.append(
                        {
                            "id": nk_idle,
                            "kind": "card",
                            "severity": "info",
                            "title": f"评审间隔提醒：{std.work_scene}",
                            "body": f"上次评审已超过 {REVIEW_IDLE_DAYS} 天，可在有新草稿时发起交付物评审。",
                            "action_label": "工作档案",
                            "action_href": "/ui/work",
                        }
                    )

    return {"banners": banners, "cards": cards}
