import uuid

from fastapi.testclient import TestClient


def _register_and_login(client: TestClient) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={"email": "bp@example.com", "password": "testpassword123"},
    )
    r = client.post(
        "/api/v1/auth/login",
        json={"email": "bp@example.com", "password": "testpassword123"},
    )
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


def test_behavior_profiler_flow_stub_llm(client: TestClient):
    h = _register_and_login(client)
    m = client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "Lead", "role": "Manager"},
    ).json()
    mid = m["id"]

    st = client.post(
        "/api/v1/ai/behavior-profiler/start",
        headers=h,
        json={"member_id": mid},
    )
    assert st.status_code == 201
    sid = st.json()["session_id"]
    assert "greeting" in st.json()

    ch = client.post(
        "/api/v1/ai/behavior-profiler/chat",
        headers=h,
        json={"session_id": sid, "user_message": "他在会上总是要求看数据。"},
    )
    assert ch.status_code == 200
    body = ch.json()
    assert "reply" in body
    assert "suggested_tags" in body

    fin = client.post(
        "/api/v1/ai/behavior-profiler/finalize",
        headers=h,
        json={"session_id": sid},
    )
    assert fin.status_code == 200
    tags = fin.json()["tags"]
    assert fin.json()["update_mode"] in ("increment", "overwrite")
    assert len(tags) >= 1
    assert all("label" in t and "confidence" in t for t in tags)

    save = client.post(
        f"/api/v1/team/members/{mid}/behavior-tags",
        headers=h,
        json={
            "tags": tags,
            "update_mode": "overwrite",
        },
    )
    assert save.status_code == 200
    saved = save.json()
    assert len(saved) == len(tags)

    member = client.get(f"/api/v1/team/members/{mid}", headers=h).json()
    assert len(member["behavior_tags"]) == len(tags)


def test_finalize_without_chat_fails(client: TestClient):
    h = _register_and_login(client)
    m = client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "X", "role": "Y"},
    ).json()
    st = client.post(
        "/api/v1/ai/behavior-profiler/start",
        headers=h,
        json={"member_id": m["id"]},
    ).json()
    fin = client.post(
        "/api/v1/ai/behavior-profiler/finalize",
        headers=h,
        json={"session_id": st["session_id"]},
    )
    assert fin.status_code == 400


def test_unknown_session_chat(client: TestClient):
    h = _register_and_login(client)
    r = client.post(
        "/api/v1/ai/behavior-profiler/chat",
        headers=h,
        json={
            "session_id": str(uuid.uuid4()),
            "user_message": "hello",
        },
    )
    assert r.status_code == 404


def test_confirm_tags_increment_updates_existing(client: TestClient):
    h = _register_and_login(client)
    m = client.post(
        "/api/v1/team/members",
        headers=h,
        json={"name": "Z", "role": "R"},
    ).json()
    mid = m["id"]
    client.post(
        f"/api/v1/team/members/{mid}/behavior-tags",
        headers=h,
        json={
            "tags": [
                {
                    "label": "Same",
                    "category": "c1",
                    "confidence": 0.5,
                    "source": "manual",
                }
            ],
            "update_mode": "overwrite",
        },
    )
    client.post(
        f"/api/v1/team/members/{mid}/behavior-tags",
        headers=h,
        json={
            "tags": [
                {
                    "label": "Same",
                    "category": "c2",
                    "confidence": 0.9,
                    "source": "ai",
                }
            ],
            "update_mode": "increment",
        },
    )
    member = client.get(f"/api/v1/team/members/{mid}", headers=h).json()
    assert len(member["behavior_tags"]) == 1
    assert member["behavior_tags"][0]["category"] == "c2"
    assert member["behavior_tags"][0]["confidence"] == 0.9
