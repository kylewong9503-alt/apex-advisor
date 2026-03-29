"""Layer 4 industry intelligence API tests."""

import pytest
from fastapi.testclient import TestClient

LONG = (
    "行业研报摘要：本季度 SaaS 渗透率提升，头部厂商集中度上升，"
    "监管侧对数据出境提出更细颗粒度要求，市场规模约千亿元级别。"
)


@pytest.fixture
def industry_auth_headers(client: TestClient) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={"email": "industrylayer@example.com", "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "industrylayer@example.com", "password": "testpassword123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_industry_knowledge_base_empty(client: TestClient, industry_auth_headers: dict):
    r = client.get("/api/v1/industry/knowledge-base", headers=industry_auth_headers)
    assert r.status_code == 200
    d = r.json()
    assert "industry_profile_id" in d
    assert d["pending_digest"] is None
    for dim in (
        "industry_trends",
        "competitive_landscape",
        "regulatory_dynamics",
        "data_highlights",
    ):
        assert d["intelligence_by_dimension"][dim] == []


def test_input_too_short(client: TestClient, industry_auth_headers: dict):
    cr = client.post(
        "/api/v1/industry/industry-inputs",
        headers=industry_auth_headers,
        json={"items": [{"raw_text": "short", "source_type": "other"}]},
    )
    assert cr.status_code == 201
    iid = cr.json()[0]["id"]
    cl = client.post(
        "/api/v1/industry/industry/classify",
        headers=industry_auth_headers,
        json={"input_ids": [iid]},
    )
    assert cl.status_code == 400


def test_classify_confirm_banner_topics_reminder(client: TestClient, industry_auth_headers: dict):
    cr = client.post(
        "/api/v1/industry/industry-inputs",
        headers=industry_auth_headers,
        json={"items": [{"raw_text": LONG, "source_type": "research"}]},
    )
    assert cr.status_code == 201
    iid = cr.json()[0]["id"]

    cl = client.post(
        "/api/v1/industry/industry/classify",
        headers=industry_auth_headers,
        json={"input_ids": [iid]},
    )
    assert cl.status_code == 200
    digest = cl.json()
    assert digest["status"] == "pending_review"
    assert digest["dimension_annotations"]

    conf = client.post(
        f"/api/v1/industry/industry/digests/{digest['id']}/confirm",
        headers=industry_auth_headers,
        json={},
    )
    assert conf.status_code == 200
    body = conf.json()
    assert len(body["industry_items"]) >= 1
    assert len(body["topic_materials"]) >= 1

    kb = client.get("/api/v1/industry/knowledge-base", headers=industry_auth_headers).json()
    assert kb["pending_digest"] is None
    total = sum(len(v) for v in kb["intelligence_by_dimension"].values())
    assert total >= 1
    assert len(kb["insights_banner"]) >= 1
    assert len(kb["topic_materials"]) >= 1

    tm = client.get(
        "/api/v1/industry/topic-materials",
        headers=industry_auth_headers,
        params={"tag": "政策"},
    )
    assert tm.status_code == 200
    assert len(tm.json()) >= 1

    rm = client.post(
        "/api/v1/industry/update-reminders",
        headers=industry_auth_headers,
        json={"dimension": "industry_trends", "frequency": "weekly"},
    )
    assert rm.status_code == 201
    rm2 = client.post(
        "/api/v1/industry/update-reminders",
        headers=industry_auth_headers,
        json={"dimension": "industry_trends", "frequency": "monthly"},
    )
    assert rm2.status_code == 201
    lst = client.get("/api/v1/industry/update-reminders", headers=industry_auth_headers)
    assert len(lst.json()) == 1
    assert lst.json()[0]["frequency"] == "monthly"


def test_industry_routes_require_auth(client: TestClient):
    assert client.get("/api/v1/industry/knowledge-base").status_code == 401
