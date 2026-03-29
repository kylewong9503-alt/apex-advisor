"""Layer 2 company intelligence API tests."""

import pytest
from fastapi.testclient import TestClient

LONG_TEXT = (
    "本次季度经营会议摘要：营收同比增长 twelve percent，"
    "管理层强调降本增效与区域扩张并举，文化上继续推动透明沟通。"
    "内部审批流程要求预算超过五十万需双签。"
)


@pytest.fixture
def company_auth_headers(client: TestClient) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={"email": "companylayer@example.com", "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "companylayer@example.com", "password": "testpassword123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_knowledge_base_empty(client: TestClient, company_auth_headers: dict):
    r = client.get("/api/v1/company/knowledge-base", headers=company_auth_headers)
    assert r.status_code == 200
    data = r.json()
    assert "company_profile_id" in data
    assert data["pending_digest"] is None
    assert data["insights_banner"] == []
    for d in (
        "business_performance",
        "strategic_direction",
        "cultural_signals",
        "internal_rules",
    ):
        assert data["intelligence_by_dimension"][d] == []


def test_intelligence_input_too_short_for_classify(client: TestClient, company_auth_headers: dict):
    cr = client.post(
        "/api/v1/company/intelligence-inputs",
        headers=company_auth_headers,
        json={"items": [{"raw_text": "short", "source_type": "other"}]},
    )
    assert cr.status_code == 201
    iid = cr.json()[0]["id"]
    r = client.post(
        "/api/v1/company/intelligence/classify",
        headers=company_auth_headers,
        json={"input_ids": [iid]},
    )
    assert r.status_code == 400
    assert "50" in r.json()["detail"] or "short" in r.json()["detail"].lower()


def test_classify_confirm_and_banner(client: TestClient, company_auth_headers: dict):
    cr = client.post(
        "/api/v1/company/intelligence-inputs",
        headers=company_auth_headers,
        json={"items": [{"raw_text": LONG_TEXT, "source_type": "meeting"}]},
    )
    assert cr.status_code == 201
    iid = cr.json()[0]["id"]

    cl = client.post(
        "/api/v1/company/intelligence/classify",
        headers=company_auth_headers,
        json={"input_ids": [iid]},
    )
    assert cl.status_code == 200
    digest = cl.json()
    assert digest["status"] == "pending_review"
    assert digest["dimension_summaries"]

    kb = client.get("/api/v1/company/knowledge-base", headers=company_auth_headers).json()
    assert kb["pending_digest"] is not None

    conf = client.post(
        f"/api/v1/company/intelligence/digests/{digest['id']}/confirm",
        headers=company_auth_headers,
        json={},
    )
    assert conf.status_code == 200
    items = conf.json()
    assert len(items) >= 1

    kb2 = client.get("/api/v1/company/knowledge-base", headers=company_auth_headers).json()
    assert kb2["pending_digest"] is None
    total = sum(len(v) for v in kb2["intelligence_by_dimension"].values())
    assert total >= 1
    assert len(kb2["insights_banner"]) >= 1


def test_stakeholder_crud(client: TestClient, company_auth_headers: dict):
    c = client.post(
        "/api/v1/company/stakeholders",
        headers=company_auth_headers,
        json={
            "name": "王主任",
            "department": "战略部",
            "influence_description": "预算审批关键人",
        },
    )
    assert c.status_code == 201
    sid = c.json()["id"]

    lst = client.get("/api/v1/company/stakeholders", headers=company_auth_headers)
    assert lst.status_code == 200
    assert len(lst.json()) == 1

    u = client.patch(
        f"/api/v1/company/stakeholders/{sid}",
        headers=company_auth_headers,
        json={"name": "王主任（更新）"},
    )
    assert u.status_code == 200
    assert u.json()["name"] == "王主任（更新）"

    d = client.delete(f"/api/v1/company/stakeholders/{sid}", headers=company_auth_headers)
    assert d.status_code == 204
    assert client.get("/api/v1/company/stakeholders", headers=company_auth_headers).json() == []


def test_company_routes_require_auth(client: TestClient):
    assert client.get("/api/v1/company/knowledge-base").status_code == 401
