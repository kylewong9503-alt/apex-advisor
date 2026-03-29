"""Prompt Engine and saved templates API tests."""

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def prompt_auth_headers(client: TestClient) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={"email": "promptuser@example.com", "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "promptuser@example.com", "password": "testpassword123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_list_scenes(client: TestClient, prompt_auth_headers: dict):
    r = client.get("/api/v1/prompts/scenes", headers=prompt_auth_headers)
    assert r.status_code == 200
    d = r.json()
    assert "communication_report" in d
    assert "沟通与汇报" in d["communication_report"]


def test_generate_prompt(client: TestClient, prompt_auth_headers: dict):
    r = client.post(
        "/api/v1/prompts/generate",
        headers=prompt_auth_headers,
        json={"scene": "communication_report"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["scene"] == "communication_report"
    assert "template" in body
    assert len(body["template"]) > 10


def test_generate_invalid_scene(client: TestClient, prompt_auth_headers: dict):
    r = client.post(
        "/api/v1/prompts/generate",
        headers=prompt_auth_headers,
        json={"scene": "not_a_scene"},
    )
    assert r.status_code == 422


def test_saved_template_crud_and_stale(client: TestClient, prompt_auth_headers: dict):
    m = client.post(
        "/api/v1/team/members",
        headers=prompt_auth_headers,
        json={"name": "Lead", "role": "Mgr", "taboo": "no jokes"},
    ).json()

    g = client.post(
        "/api/v1/prompts/generate",
        headers=prompt_auth_headers,
        json={"scene": "research"},
    )
    text = g.json()["template"]
    c = client.post(
        "/api/v1/prompts/templates",
        headers=prompt_auth_headers,
        json={"name": "我的调研模板", "scene": "research", "body": text},
    )
    assert c.status_code == 201
    tid = c.json()["id"]
    assert c.json()["needs_refresh"] is False
    assert c.json()["version"] == 1

    client.patch(
        f"/api/v1/team/members/{m['id']}",
        headers=prompt_auth_headers,
        json={"notes": "profile change after template save"},
    )
    lst = client.get("/api/v1/prompts/templates", headers=prompt_auth_headers)
    assert lst.status_code == 200
    rows = lst.json()
    mine = next(x for x in rows if x["id"] == str(tid))
    assert mine["needs_refresh"] is True

    sync = client.post(
        f"/api/v1/prompts/templates/{tid}/mark-synced",
        headers=prompt_auth_headers,
    )
    assert sync.status_code == 200
    assert sync.json()["needs_refresh"] is False

    u = client.patch(
        f"/api/v1/prompts/templates/{tid}",
        headers=prompt_auth_headers,
        json={"body": text + "\n\n附注"},
    )
    assert u.status_code == 200
    assert u.json()["version"] == 2

    d = client.delete(f"/api/v1/prompts/templates/{tid}", headers=prompt_auth_headers)
    assert d.status_code == 204


def test_prompt_routes_require_auth(client: TestClient):
    assert client.get("/api/v1/prompts/scenes").status_code == 401
