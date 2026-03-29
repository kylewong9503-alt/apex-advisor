from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import func as sqlfunc
from sqlalchemy.orm import Session

from app.models.company import CompanyProfile, IntelligenceItem
from app.models.industry import IndustryItem, IndustryProfile
from app.models.team import TeamMember
from app.models.work import WorkProfile
from app.services.ai.base import BaseLLMService, get_llm_service
from app.services.profile_service import ProfileService

PROMPT_ENGINE_SYSTEM = (
    "[prompt_engine]\n"
    "You are Prompt_Engine. Given a structured context bundle for a workplace Scene, "
    "produce a single ready-to-use Prompt_Template in the user's language (Chinese if "
    "context is Chinese). Include sections for role, constraints, and references to "
    "profile facts. Preserve Taboo constraints verbatim when provided.\n"
    "Respond with JSON only:\n"
    '{\n'
    '  "template": "<full prompt text>",\n'
    '  "placeholder_hints": ["<optional hints for missing data>"]\n'
    "}\n"
)

# Requirement 6.2 — five preset scenes
PROMPT_SCENES: Dict[str, str] = {
    "communication_report": "沟通与汇报",
    "meeting_presentation": "会议与展示准备",
    "materials_proposal": "材料组织与提案撰写",
    "research": "研究与调研实施",
    "project_planning": "项目进度规划与管理",
}


def _parse_json_object(text: str) -> dict:
    text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("No JSON object in model output")
    return json.loads(text[start : end + 1])


def _utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def max_profile_updated_at(db: Session, user_id: uuid.UUID) -> Optional[datetime]:
    """Latest update timestamp across four layers + team members."""
    times: List[datetime] = []

    cu = (
        db.query(sqlfunc.max(CompanyProfile.updated_at))
        .filter(CompanyProfile.user_id == user_id)
        .scalar()
    )
    if cu:
        times.append(_utc(cu))

    wu = (
        db.query(sqlfunc.max(WorkProfile.updated_at))
        .filter(WorkProfile.user_id == user_id)
        .scalar()
    )
    if wu:
        times.append(_utc(wu))

    iu = (
        db.query(sqlfunc.max(IndustryProfile.updated_at))
        .filter(IndustryProfile.user_id == user_id)
        .scalar()
    )
    if iu:
        times.append(_utc(iu))

    mu = (
        db.query(sqlfunc.max(TeamMember.updated_at))
        .filter(TeamMember.user_id == user_id)
        .scalar()
    )
    if mu:
        times.append(_utc(mu))

    # Child rows do not bump parent profile rows; include for stale detection (req 6.7).
    ci = (
        db.query(sqlfunc.max(IntelligenceItem.updated_at))
        .join(CompanyProfile, IntelligenceItem.company_profile_id == CompanyProfile.id)
        .filter(CompanyProfile.user_id == user_id)
        .scalar()
    )
    if ci:
        times.append(_utc(ci))

    ii = (
        db.query(sqlfunc.max(IndustryItem.updated_at))
        .join(IndustryProfile, IndustryItem.industry_profile_id == IndustryProfile.id)
        .filter(IndustryProfile.user_id == user_id)
        .scalar()
    )
    if ii:
        times.append(_utc(ii))

    return max(times) if times else None


def template_needs_refresh(
    db: Session, user_id: uuid.UUID, last_synced_at: Optional[datetime]
) -> bool:
    """True if any profile data changed after last_synced_at (Requirement 6.7)."""
    max_ts = max_profile_updated_at(db, user_id)
    if max_ts is None or last_synced_at is None:
        return False
    return max_ts > _utc(last_synced_at)


def _summarize_team(profiles: Dict[str, Any], member_id: Optional[uuid.UUID]) -> str:
    members = profiles.get("team_profile", {}).get("members") or []
    lines: List[str] = []
    for m in members:
        mid = m.get("id")
        if member_id and str(member_id) != str(mid):
            continue
        tags = ", ".join(
            f"{t.get('label', '')}({t.get('confidence', 0):.0%})"
            for t in (m.get("behavior_tags") or [])[:12]
        )
        taboo = (m.get("taboo") or "").strip()
        taboo_s = taboo if taboo else "[待补充：该成员的禁忌雷区 Taboo]"
        lines.append(
            f"- {m.get('name')} ({m.get('role')}): 沟通偏好={m.get('communication_preference') or '—'}; "
            f"标签: {tags or '—'}; 禁忌: {taboo_s}"
        )
    if not lines:
        return "[待补充：团队成员与行为标签]"
    return "\n".join(lines)


def _summarize_company(profiles: Dict[str, Any]) -> str:
    intel = profiles.get("company_profile", {}).get("intelligence") or {}
    parts: List[str] = []
    for dim, items in intel.items():
        if not items:
            parts.append(f"- {dim}: [待补充]")
        else:
            blob = "; ".join((x.get("content") or "")[:200] for x in items[:3])
            parts.append(f"- {dim}: {blob}")
    return "\n".join(parts) if parts else "[待补充：公司情报]"


def _summarize_work(profiles: Dict[str, Any], scene: str) -> str:
    wp = profiles.get("work_profile") or {}
    stds = wp.get("deliverable_standards") or []
    if not stds:
        return "[待补充：交付物标准 / 工作场景]"
    # Prefer first standard or match scene loosely
    chunks = []
    for s in stds[:5]:
        chunks.append(
            f"场景 {s.get('work_scene')}: 格式={s.get('format_requirements') or '—'}; "
            f"体例={s.get('style_requirements') or '—'}"
        )
    return "\n".join(chunks)


def _summarize_industry(profiles: Dict[str, Any]) -> str:
    intel = profiles.get("industry_profile", {}).get("intelligence") or {}
    parts: List[str] = []
    for dim, items in intel.items():
        if not items:
            parts.append(f"- {dim}: [待补充]")
        else:
            blob = "; ".join((x.get("content") or "")[:200] for x in items[:3])
            parts.append(f"- {dim}: {blob}")
    return "\n".join(parts) if parts else "[待补充：行业情报]"


def _taboo_block(db: Session, user_id: uuid.UUID, member_id: Optional[uuid.UUID]) -> str:
    if not member_id:
        return ""
    m = (
        db.query(TeamMember)
        .filter(TeamMember.id == member_id, TeamMember.user_id == user_id)
        .first()
    )
    if not m:
        return ""
    name = m.name
    taboo = (m.taboo or "").strip()
    if taboo:
        return (
            f"### 禁忌约束（针对 {name}）\n"
            f"以下内容必须在生成结果中规避或谨慎处理：\n{taboo}\n"
        )
    return (
        f"### 禁忌约束（针对 {name}）\n"
        "[提示：尚未记录该成员的禁忌雷区，建议补充 Taboo 信息以降低沟通风险。]\n"
    )


def build_context_bundle(
    db: Session,
    *,
    user_id: uuid.UUID,
    scene: str,
    team_member_id: Optional[uuid.UUID] = None,
) -> str:
    ps = ProfileService(db)
    profiles = ps.get_user_profiles(user_id)
    scene_label = PROMPT_SCENES.get(scene, scene)
    bundle = [
        f"【场景 Scene】{scene_label} ({scene})",
        "",
        "【团队 Team】",
        _summarize_team(profiles, team_member_id),
        "",
        "【公司 Company】",
        _summarize_company(profiles),
        "",
        "【工作 Work】",
        _summarize_work(profiles, scene),
        "",
        "【行业 Industry】",
        _summarize_industry(profiles),
        "",
        "【Taboo 约束】",
        _taboo_block(db, user_id, team_member_id) or "(未指定具体成员：可省略禁忌块)",
    ]
    return "\n".join(bundle)


class PromptEngine:
    def __init__(self, llm: Optional[BaseLLMService] = None):
        self._llm = llm

    def _client(self) -> BaseLLMService:
        return self._llm if self._llm is not None else get_llm_service()

    async def _complete(self, user_prompt: str) -> str:
        return await self._client().generate_response(
            user_prompt,
            system_message=PROMPT_ENGINE_SYSTEM,
        )

    async def generate(
        self,
        db: Session,
        *,
        user_id: uuid.UUID,
        scene: str,
        team_member_id: Optional[uuid.UUID] = None,
    ) -> Tuple[str, List[str]]:
        if scene not in PROMPT_SCENES:
            raise ValueError(f"Unknown scene: {scene}")
        bundle = build_context_bundle(
            db, user_id=user_id, scene=scene, team_member_id=team_member_id
        )
        raw = await self._complete(bundle)
        data = _parse_json_object(raw)
        template = str(data.get("template") or "").strip()
        if not template:
            raise ValueError("Empty template from model")
        hints = data.get("placeholder_hints") or []
        ph = [str(x) for x in hints] if isinstance(hints, list) else []
        return template, ph
