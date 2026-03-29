from fastapi.testclient import TestClient


def test_create_team_member(client: TestClient, auth_headers: dict):
    r = client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "Alex Chen", "role": "Engineering Lead"},
    )
    assert r.status_code == 201
    data = r.json()
    assert data["name"] == "Alex Chen"
    assert data["role"] == "Engineering Lead"
    assert data["behavior_tags"] == []


def test_create_team_member_whitespace_name_rejected(client: TestClient, auth_headers: dict):
    r = client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "   ", "role": "Lead"},
    )
    assert r.status_code == 422


def test_list_team_members(client: TestClient, auth_headers: dict):
    client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "A", "role": "R1"},
    )
    client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "B", "role": "R2"},
    )
    r = client.get("/api/v1/team/members", headers=auth_headers)
    assert r.status_code == 200
    assert len(r.json()) == 2


def test_get_update_delete_team_member(client: TestClient, auth_headers: dict):
    created = client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "Pat", "role": "PM"},
    ).json()
    mid = created["id"]

    g = client.get(f"/api/v1/team/members/{mid}", headers=auth_headers)
    assert g.status_code == 200
    assert g.json()["name"] == "Pat"

    u = client.patch(
        f"/api/v1/team/members/{mid}",
        headers=auth_headers,
        json={"name": "Pat Lee"},
    )
    assert u.status_code == 200
    assert u.json()["name"] == "Pat Lee"

    d = client.delete(f"/api/v1/team/members/{mid}", headers=auth_headers)
    assert d.status_code == 204

    missing = client.get(f"/api/v1/team/members/{mid}", headers=auth_headers)
    assert missing.status_code == 404


def test_team_member_not_accessible_to_other_user(client: TestClient, auth_headers: dict):
    created = client.post(
        "/api/v1/team/members",
        headers=auth_headers,
        json={"name": "Secret", "role": "X"},
    ).json()
    mid = created["id"]

    client.post(
        "/api/v1/auth/register",
        json={"email": "other@example.com", "password": "testpassword123"},
    )
    other_login = client.post(
        "/api/v1/auth/login",
        json={"email": "other@example.com", "password": "testpassword123"},
    )
    other_headers = {"Authorization": f"Bearer {other_login.json()['access_token']}"}

    r = client.get(f"/api/v1/team/members/{mid}", headers=other_headers)
    assert r.status_code == 404


def test_team_routes_require_auth(client: TestClient):
    r = client.get("/api/v1/team/members")
    assert r.status_code == 401
