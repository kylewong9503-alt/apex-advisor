import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.db.database import Base, get_db
from app.services.ai.base import StubLLMService


@pytest.fixture(autouse=True)
def _stub_llm_for_tests(monkeypatch):
    """Avoid real OpenAI calls when OPENAI_API_KEY is set but invalid or missing in CI."""
    stub = StubLLMService()
    monkeypatch.setattr(
        "app.services.ai.behavior_profiler.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.ai.intelligence_classifier.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.ai.deliverable_review.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.ai.followup_extractor.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.ai.industry_classifier.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.ai.prompt_engine.get_llm_service",
        lambda: stub,
    )
    monkeypatch.setattr(
        "app.services.chatbox_service.get_llm_service",
        lambda: stub,
    )


@pytest.fixture
def client():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = TestSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


@pytest.fixture
def auth_headers(client: TestClient):
    client.post(
        "/api/v1/auth/register",
        json={"email": "teamowner@example.com", "password": "testpassword123"},
    )
    login = client.post(
        "/api/v1/auth/login",
        json={"email": "teamowner@example.com", "password": "testpassword123"},
    )
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}
