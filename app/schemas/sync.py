from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Literal, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SyncStatusResponse(BaseModel):
    """Server clock + revision + polling targets (8.1, 8.2)."""

    server_time: datetime
    revision: str
    persist_target_ms: int = 1000
    cross_device_poll_sec: int = 5
    offline_mode_supported: bool = True


class SyncDeviceRegister(BaseModel):
    device_id: str = Field(..., min_length=4, max_length=64)
    label: Optional[str] = Field(None, max_length=255)


class SyncDeviceItem(BaseModel):
    device_id: str
    label: Optional[str] = None
    last_seen_at: datetime
    last_ack_revision: Optional[str] = None


class SyncBundleResponse(BaseModel):
    """Full profile snapshot for offline cache refresh (8.3)."""

    revision: str
    generated_at: datetime
    profiles: Dict[str, Any]


class TeamMemberPatchMutation(BaseModel):
    op: Literal["team_member.patch"]
    id: UUID
    expected_updated_at: datetime
    fields: Dict[str, Any] = Field(default_factory=dict)


class SyncPushRequest(BaseModel):
    device_id: str = Field(..., min_length=4, max_length=64)
    base_revision: str = Field(..., description="Revision from last successful bundle/pull")
    mutations: List[TeamMemberPatchMutation] = Field(default_factory=list)


class SyncConflictItem(BaseModel):
    resource: str
    resource_id: UUID
    reason: str
    server_updated_at: datetime
    server_snapshot: Dict[str, Any]


class SyncPushResponse(BaseModel):
    ok: bool
    new_revision: str
    applied: int = 0
    conflicts: List[SyncConflictItem] = Field(default_factory=list)


class SyncRevisionStaleResponse(BaseModel):
    """Whole-bundle conflict: client must re-pull (8.4)."""

    code: Literal["revision_stale"] = "revision_stale"
    current_revision: str
    bundle: SyncBundleResponse


class OfflineHintsResponse(BaseModel):
    """Guidance for local SQLite offline cache (8.3)."""

    recommended_engine: str = "sqlite"
    notes: List[str] = Field(
        default_factory=lambda: [
            "Cache GET /api/v1/sync/bundle after login; store revision string.",
            "Apply edits offline against cached rows; queue mutations with expected_updated_at from cache.",
            "On reconnect POST /api/v1/sync/push with base_revision; on revision_stale re-fetch bundle.",
            "Poll GET /api/v1/sync/status every cross_device_poll_sec for cross-device updates.",
        ]
    )
