"""Layer 3 work profile API tests."""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

DRAFT = "这是一份足够长的草稿内容，用于测试交付物评审流程。" * 3


@pytest.fixture
def work_auth_headers(client: TestClient) -> dict:
    client.post(
        "/api/v1/auth/register",
        json={"email": "worklayer@example.com", "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "worklayer@example.com", "password": "testpassword123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def test_work_knowledge_base_empty(client: TestClient, work_auth_headers: dict):
    r = client.get("/api/v1/work/knowledge-base", headers=work_auth_headers)
    assert r.status_code == 200
    d = r.json()
    assert "work_profile_id" in d
    assert d["deliverable_standards"] == []
    assert d["follow_up_items"] == []


def test_deliverable_standard_crud(client: TestClient, work_auth_headers: dict):
    c = client.post(
        "/api/v1/work/deliverable-standards",
        headers=work_auth_headers,
        json={
            "work_scene": "向上汇报 PPT",
            "format_requirements": "16:9",
            "style_requirements": "结论前置",
        },
    )
    assert c.status_code == 201
    sid = c.json()["id"]

    g = client.get(f"/api/v1/work/deliverable-standards/{sid}", headers=work_auth_headers)
    assert g.status_code == 200
    assert g.json()["work_scene"] == "向上汇报 PPT"

    u = client.patch(
        f"/api/v1/work/deliverable-standards/{sid}",
        headers=work_auth_headers,
        json={"work_scene": "汇报 PPT"},
    )
    assert u.status_code == 200
    assert u.json()["work_scene"] == "汇报 PPT"

    d = client.delete(f"/api/v1/work/deliverable-standards/{sid}", headers=work_auth_headers)
    assert d.status_code == 204


def test_review_and_pattern_after_three_runs(client: TestClient, work_auth_headers: dict):
    c = client.post(
        "/api/v1/work/deliverable-standards",
        headers=work_auth_headers,
        json={"work_scene": "战略分析", "format_requirements": "Word"},
    )
    sid = c.json()["id"]

    for _ in range(3):
        rv = client.post(
            f"/api/v1/work/deliverable-standards/{sid}/review",
            headers=work_auth_headers,
            json={"draft_text": DRAFT},
        )
        assert rv.status_code == 200
        body = rv.json()
        assert body["overall_compliance"] >= 0
        assert len(body["dimensions"]) == 5
        assert body["pattern_recomputed"] is (_ == 2)

    pat = client.get(
        f"/api/v1/work/deliverable-standards/{sid}/pattern",
        headers=work_auth_headers,
    )
    assert pat.status_code == 200
    p = pat.json()
    assert p is not None
    assert p["based_on_count"] == 3
    assert isinstance(p["persistent_issues"], list)

    hist = client.get(
        f"/api/v1/work/deliverable-standards/{sid}/review-history",
        headers=work_auth_headers,
    )
    assert len(hist.json()) == 3


def test_followup_extract_and_confirm(client: TestClient, work_auth_headers: dict):
    ws = client.post(
        "/api/v1/work/workstreams",
        headers=work_auth_headers,
        json={"name": "产品迭代"},
    )
    assert ws.status_code == 201

    note = client.post(
        "/api/v1/work/meeting-notes",
        headers=work_auth_headers,
        json={
            "title": "周会",
            "meeting_date": datetime.now(timezone.utc).isoformat(),
            "content": "讨论了里程碑与后续行动项，需要完成复盘文档并同步项目组。",
        },
    )
    assert note.status_code == 201
    nid = note.json()["id"]

    ex = client.post(
        f"/api/v1/work/meeting-notes/{nid}/extract-followups",
        headers=work_auth_headers,
    )
    assert ex.status_code == 200
    drafts = ex.json()["drafts"]
    assert len(drafts) >= 1

    cf = client.post(
        f"/api/v1/work/meeting-notes/{nid}/confirm-followups",
        headers=work_auth_headers,
        json={"items": drafts},
    )
    assert cf.status_code == 200
    assert len(cf.json()) >= 1

    fu = client.get("/api/v1/work/follow-ups", headers=work_auth_headers, params={"status": "pending"})
    assert len(fu.json()) >= 1


def test_work_routes_require_auth(client: TestClient):
    assert client.get("/api/v1/work/knowledge-base").status_code == 401
