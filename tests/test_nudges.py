"""Contextual nudge API (Task 11)."""

import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient


def _auth(client: TestClient) -> dict:
    email = f"nudge-{uuid.uuid4().hex}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_nudges_returns_structure(client: TestClient):
    h = _auth(client)
    r = client.get("/api/v1/nudges", headers=h)
    assert r.status_code == 200
    d = r.json()
    assert "banners" in d and "cards" in d
    assert isinstance(d["banners"], list)
    assert isinstance(d["cards"], list)


def test_profile_stale_banner_then_dismiss(client: TestClient, monkeypatch):
    h = _auth(client)
    old = datetime.now(timezone.utc) - timedelta(days=20)
    monkeypatch.setattr(
        "app.services.nudge_service.max_profile_updated_at",
        lambda db, uid: old,
    )

    r = client.get("/api/v1/nudges", headers=h)
    assert r.status_code == 200
    banners = r.json()["banners"]
    stale = [b for b in banners if b["id"] == "profile_stale_14d"]
    assert len(stale) == 1

    d = client.post(
        "/api/v1/nudges/dismiss",
        headers=h,
        json={"nudge_key": "profile_stale_14d"},
    )
    assert d.status_code == 204

    r2 = client.get("/api/v1/nudges", headers=h)
    assert not any(b["id"] == "profile_stale_14d" for b in r2.json()["banners"])


def test_nudges_require_auth(client: TestClient):
    assert client.get("/api/v1/nudges").status_code == 401
