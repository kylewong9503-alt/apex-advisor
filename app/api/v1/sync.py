from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.api.deps import get_current_active_user
from app.db.database import get_db
from app.models.sync_device import SyncDevice
from app.models.user import User
from app.schemas.sync import (
    OfflineHintsResponse,
    SyncBundleResponse,
    SyncConflictItem,
    SyncDeviceItem,
    SyncDeviceRegister,
    SyncPushRequest,
    SyncPushResponse,
    SyncRevisionStaleResponse,
    SyncStatusResponse,
)
from app.services.sync_service import (
    apply_team_member_patches,
    build_bundle,
    compute_revision,
    revisions_match,
)

router = APIRouter()


@router.get("/status", response_model=SyncStatusResponse)
def sync_status(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Server time, global revision, and polling targets (8.1)."""
    now = datetime.now(timezone.utc)
    return SyncStatusResponse(
        server_time=now,
        revision=compute_revision(db, current_user.id),
    )


@router.get("/offline-hints", response_model=OfflineHintsResponse)
def offline_hints(
    current_user: User = Depends(get_current_active_user),
):
    """Client-side SQLite / offline queue guidance (8.3)."""
    return OfflineHintsResponse()


@router.get("/bundle", response_model=SyncBundleResponse)
def sync_bundle(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """Full profile JSON for local cache + conflict baseline revision."""
    rev, gen, profiles = build_bundle(db, current_user.id)
    return SyncBundleResponse(revision=rev, generated_at=gen, profiles=profiles)


@router.post("/devices/register", response_model=SyncDeviceItem, status_code=status.HTTP_201_CREATED)
def register_device(
    body: SyncDeviceRegister,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    row = (
        db.query(SyncDevice)
        .filter(
            SyncDevice.user_id == current_user.id,
            SyncDevice.device_id == body.device_id.strip(),
        )
        .first()
    )
    if row:
        row.label = (body.label or "").strip() or None
        row.last_seen_at = datetime.now(timezone.utc)
    else:
        row = SyncDevice(
            user_id=current_user.id,
            device_id=body.device_id.strip(),
            label=(body.label or "").strip() or None,
        )
        db.add(row)
    db.commit()
    db.refresh(row)
    return SyncDeviceItem(
        device_id=row.device_id,
        label=row.label,
        last_seen_at=row.last_seen_at,
        last_ack_revision=row.last_ack_revision,
    )


@router.get("/devices", response_model=list[SyncDeviceItem])
def list_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    rows = (
        db.query(SyncDevice)
        .filter(SyncDevice.user_id == current_user.id)
        .order_by(SyncDevice.last_seen_at.desc())
        .all()
    )
    return [
        SyncDeviceItem(
            device_id=r.device_id,
            label=r.label,
            last_seen_at=r.last_seen_at,
            last_ack_revision=r.last_ack_revision,
        )
        for r in rows
    ]


@router.post("/push")
def sync_push(
    body: SyncPushRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """
    Apply queued offline mutations with optimistic concurrency (8.4).
    Returns 409 + bundle if base_revision is stale.
    """
    current = compute_revision(db, current_user.id)
    if not revisions_match(body.base_revision, current):
        rev, gen, profiles = build_bundle(db, current_user.id)
        stale = SyncRevisionStaleResponse(
            current_revision=current,
            bundle=SyncBundleResponse(revision=rev, generated_at=gen, profiles=profiles),
        )
        return JSONResponse(
            status_code=status.HTTP_409_CONFLICT,
            content=stale.model_dump(mode="json"),
        )

    applied, conflicts = apply_team_member_patches(db, current_user.id, body.mutations)
    row = (
        db.query(SyncDevice)
        .filter(
            SyncDevice.user_id == current_user.id,
            SyncDevice.device_id == body.device_id.strip(),
        )
        .first()
    )
    if not row:
        row = SyncDevice(
            user_id=current_user.id,
            device_id=body.device_id.strip(),
        )
        db.add(row)
    row.last_seen_at = datetime.now(timezone.utc)
    db.flush()
    new_rev = compute_revision(db, current_user.id)
    row.last_ack_revision = new_rev
    db.commit()

    resp = SyncPushResponse(
        ok=len(conflicts) == 0,
        new_revision=new_rev,
        applied=applied,
        conflicts=[SyncConflictItem(**c) for c in conflicts],
    )
    return resp
