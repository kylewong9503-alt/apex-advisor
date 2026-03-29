"""Chatbox API tests (Task 10)."""

import uuid

from fastapi.testclient import TestClient


def _register_and_login(client: TestClient) -> dict:
    email = f"chat-{uuid.uuid4().hex}@example.com"
    client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "testpassword123"},
    )
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_chat_session_and_message(client: TestClient):
    h = _register_and_login(client)
    s = client.post("/api/v1/chat/sessions", json={"title": "T1"}, headers=h)
    assert s.status_code == 201
    sid = s.json()["id"]
    assert "profile_completeness" in s.json()
    pc = s.json()["profile_completeness"]
    assert "team" in pc and "company" in pc

    r = client.post(
        f"/api/v1/chat/sessions/{sid}/messages",
        headers=h,
        json={"message": "Hello", "context_tags": ["team", "company"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["user_message"]["role"] == "user"
    assert body["assistant_message"]["role"] == "assistant"
    assert body["assistant_message"]["content"]
    assert body["assistant_message"]["chain_of_thought"]
    assert body["assistant_message"]["sources_used"] is not None

    hist = client.get(f"/api/v1/chat/sessions/{sid}", headers=h)
    assert hist.status_code == 200
    assert len(hist.json()["messages"]) == 2


def test_slash_help(client: TestClient):
    h = _register_and_login(client)
    s = client.post("/api/v1/chat/sessions", json={}, headers=h).json()["id"]
    r = client.post(
        f"/api/v1/chat/sessions/{s}/messages",
        headers=h,
        json={"message": "/help"},
    )
    assert r.status_code == 200
    assert "可用命令" in r.json()["assistant_message"]["content"]


def test_slash_prompt_delegates(client: TestClient):
    h = _register_and_login(client)
    s = client.post("/api/v1/chat/sessions", json={}, headers=h).json()["id"]
    r = client.post(
        f"/api/v1/chat/sessions/{s}/messages",
        headers=h,
        json={"message": "/prompt research"},
    )
    assert r.status_code == 200
    assert "研究与调研" in r.json()["assistant_message"]["content"] or "Stub" in r.json()[
        "assistant_message"
    ]["content"]


def test_chat_commands_list(client: TestClient):
    h = _register_and_login(client)
    r = client.get("/api/v1/chat/commands", headers=h)
    assert r.status_code == 200
    data = r.json()
    names = [c["name"] for c in data["commands"]]
    assert "/help" in names and "/prompt" in names
    assert "/draft" in names and "/insights" in names
    assert data.get("help_text") and "可用命令" in data["help_text"]
    assert any(c.get("canonical") == "behavior" for c in data["commands"])


def test_chat_commands_prefix_filter(client: TestClient):
    h = _register_and_login(client)
    r = client.get("/api/v1/chat/commands?prefix=/pr", headers=h)
    assert r.status_code == 200
    names = [c["name"] for c in r.json()["commands"]]
    assert "/prompt" in names
    assert "/predict" in names
    assert "/help" not in names


def test_chat_requires_auth(client: TestClient):
    assert client.post("/api/v1/chat/sessions", json={}).status_code == 401
