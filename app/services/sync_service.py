from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy.orm import Session

from app.models.team import TeamMember
from app.services.ai.prompt_engine import max_profile_updated_at
from app.services.profile_service import ProfileService
from app.schemas.team import TeamMemberUpdate


def _utc(dt: Optional[datetime]) -> datetime:
    if dt is None:
        return datetime.fromtimestamp(0, tz=timezone.utc)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_revision(db: Session, user_id: uuid.UUID) -> str:
    m = max_profile_updated_at(db, user_id)
    if m is None:
        return "epoch"
    return _utc(m).isoformat()


def build_bundle(db: Session, user_id: uuid.UUID) -> Tuple[str, datetime, Dict[str, Any]]:
    ps = ProfileService(db)
    profiles = ps.get_user_profiles(user_id)
    now = datetime.now(timezone.utc)
    rev = compute_revision(db, user_id)
    return rev, now, profiles


def revisions_match(a: str, b: str) -> bool:
    return (a or "").strip() == (b or "").strip()


def _team_member_snapshot(m: TeamMember) -> Dict[str, Any]:
    rp = m.reporting_preference
    rp_val = rp.value if hasattr(rp, "value") else rp
    return {
        "id": str(m.id),
        "name": m.name,
        "role": m.role,
        "personality_traits": m.personality_traits,
        "communication_preference": m.communication_preference,
        "decision_style": m.decision_style,
        "reporting_preference": rp_val,
        "taboo": m.taboo,
        "notes": m.notes,
        "updated_at": _utc(m.updated_at).isoformat(),
    }


def apply_team_member_patches(
    db: Session,
    user_id: uuid.UUID,
    mutations: List[Any],
) -> Tuple[int, List[Dict[str, Any]]]:
    """Returns (applied_count, conflict_dicts)."""
    applied = 0
    conflicts: List[Dict[str, Any]] = []

    for mu in mutations:
        if mu.op != "team_member.patch":
            continue
        member = (
            db.query(TeamMember)
            .filter(TeamMember.id == mu.id, TeamMember.user_id == user_id)
            .first()
        )
        if not member:
            conflicts.append(
                {
                    "resource": "team_member",
                    "resource_id": mu.id,
                    "reason": "not_found",
                    "server_updated_at": datetime.now(timezone.utc),
                    "server_snapshot": {},
                }
            )
            continue
        server_ts = _utc(member.updated_at)
        client_ts = _utc(mu.expected_updated_at)
        if server_ts != client_ts:
            conflicts.append(
                {
                    "resource": "team_member",
                    "resource_id": mu.id,
                    "reason": "version_mismatch",
                    "server_updated_at": server_ts,
                    "server_snapshot": _team_member_snapshot(member),
                }
            )
            continue
        try:
            body = TeamMemberUpdate.model_validate(mu.fields)
        except Exception as e:
            conflicts.append(
                {
                    "resource": "team_member",
                    "resource_id": mu.id,
                    "reason": f"invalid_fields: {e}",
                    "server_updated_at": server_ts,
                    "server_snapshot": _team_member_snapshot(member),
                }
            )
            continue
        data = body.model_dump(exclude_unset=True)
        if not data:
            continue
        for field, value in data.items():
            setattr(member, field, value)
        member.updated_at = datetime.now(timezone.utc)
        applied += 1

    return applied, conflicts
