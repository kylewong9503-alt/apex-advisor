from fastapi.testclient import TestClient

from app.main import app


def test_team_archive_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/team")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Apex Advisor" in r.text
        assert "behavior-profiler" in r.text or "/ai/behavior-profiler" in r.text


def test_company_intelligence_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/company")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Apex Advisor" in r.text
        assert "/api/v1/company" in r.text or "company/knowledge-base" in r.text


def test_work_profile_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/work")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Apex Advisor" in r.text
        assert "/api/v1/work" in r.text


def test_nudges_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/nudges")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "情境提醒" in r.text
        assert "/api/v1/nudges" in r.text


def test_chatbox_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/chat")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Chatbox" in r.text
        assert '"/api/v1"' in r.text and "/chat/" in r.text


def test_industry_intelligence_ui_available():
    with TestClient(app) as client:
        r = client.get("/ui/industry")
        assert r.status_code == 200
        assert "text/html" in r.headers.get("content-type", "")
        assert "Apex Advisor" in r.text
        assert "/api/v1/industry" in r.text
