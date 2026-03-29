"""Cloud sync API (Task 12)."""

import uuid

from fastapi.testclient import TestClient


def _auth(client: TestClient) -> dict:
    email = f"sync-{uuid.uuid4().hex}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_sync_status_and_bundle(client: TestClient):
    h = _auth(client)
    st = client.get("/api/v1/sync/status", headers=h)
    assert st.status_code == 200
    assert "revision" in st.json()
    assert st.json()["persist_target_ms"] == 1000
    assert st.json()["cross_device_poll_sec"] == 5

    b = client.get("/api/v1/sync/bundle", headers=h)
    assert b.status_code == 200
    body = b.json()
    assert "revision" in body and "profiles" in body
    assert body["profiles"].get("team_profile") is not None


def test_sync_push_team_member_and_revision(client: TestClient):
    h = _auth(client)
    bundle = client.get("/api/v1/sync/bundle", headers=h).json()
    rev0 = bundle["revision"]

    m = client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "A", "role": "R"},
    ).json()
    mid = m["id"]

    bundle2 = client.get("/api/v1/sync/bundle", headers=h).json()
    rev1 = bundle2["revision"]

    r = client.post(
        "/api/v1/sync/push",
        headers=h,
        json={
            "device_id": "dev-test-1",
            "base_revision": rev1,
            "mutations": [
                {
                    "op": "team_member.patch",
                    "id": mid,
                    "expected_updated_at": m["updated_at"],
                    "fields": {"notes": "synced note"},
                }
            ],
        },
    )
    assert r.status_code == 200
    out = r.json()
    assert out["ok"] is True
    assert out["applied"] == 1
    assert out["new_revision"] != rev0

    g = client.get(f"/api/v1/team/members/{mid}", headers=h)
    assert g.json()["notes"] == "synced note"


def test_sync_push_revision_stale_409(client: TestClient):
    h = _auth(client)
    client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "B", "role": "R2"},
    )
    bundle = client.get("/api/v1/sync/bundle", headers=h).json()
    rev = bundle["revision"]

    client.patch(
        "/api/v1/team/members/" + client.get("/api/v1/team/members", headers=h).json()[0]["id"],
        headers=h,
        json={"notes": "other device"},
    )

    r = client.post(
        "/api/v1/sync/push",
        headers=h,
        json={
            "device_id": "dev-2",
            "base_revision": rev,
            "mutations": [],
        },
    )
    assert r.status_code == 409
    assert r.json()["code"] == "revision_stale"
    assert "bundle" in r.json()


def test_sync_offline_hints(client: TestClient):
    h = _auth(client)
    r = client.get("/api/v1/sync/offline-hints", headers=h)
    assert r.status_code == 200
    assert r.json()["recommended_engine"] == "sqlite"
    assert "bundle" in r.json()["notes"][0]


def test_sync_requires_auth(client: TestClient):
    assert client.get("/api/v1/sync/bundle").status_code == 401
