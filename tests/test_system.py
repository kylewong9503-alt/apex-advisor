"""System / integration metadata endpoints."""


def test_capabilities_lists_ai_modules(client):
    r = client.get("/api/v1/system/capabilities")
    assert r.status_code == 200
    d = r.json()
    assert d["profile_service"]["layers"] == ["team", "company", "work", "industry"]
    names = {m["name"] for m in d["ai_modules"]}
    assert "Prompt_Engine" in names and "Chatbox" in names
