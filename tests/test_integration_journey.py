"""
End-to-end journey: registration → profile-backed prompt generation → sync bundle (Task 13.3).
"""

import uuid

from fastapi.testclient import TestClient


def test_journey_register_prompt_sync_bundle(client: TestClient):
    email = f"journey-{uuid.uuid4().hex}@example.com"
    assert client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    ).status_code == 201

    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    assert client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "Lead", "role": "PM"},
    ).status_code == 201

    g = client.post(
        "/api/v1/prompts/generate",
        headers=h,
        json={"scene": "communication_report"},
    )
    assert g.status_code == 200
    assert len(g.json().get("template", "")) > 10

    b = client.get("/api/v1/sync/bundle", headers=h)
    assert b.status_code == 200
    assert b.json()["profiles"]["team_profile"]["members"]

    cap = client.get("/api/v1/system/capabilities")
    assert cap.status_code == 200


def test_validation_error_shape(client: TestClient):
    r = client.post("/api/v1/auth/login", json={})
    assert r.status_code == 422
    body = r.json()
    assert "error" in body
    assert body["error"]["type"] == "RequestValidationError"
    assert "details" in body["error"]
