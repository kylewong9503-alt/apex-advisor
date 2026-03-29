import pytest
from fastapi.testclient import TestClient
from app.models.user import User


def test_register_user(client: TestClient):
    """Test user registration creates user and empty profiles."""
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )

    assert response.status_code == 201
    data = response.json()
    assert data["email"] == "test@example.com"
    assert data["is_active"] is True
    assert "id" in data
    assert "created_at" in data


def test_register_creates_four_layer_profiles():
    """Registration initializes company, work, and industry profile rows (team layer is empty list)."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from sqlalchemy.pool import StaticPool
    from fastapi.testclient import TestClient

    from app.db.database import Base, get_db
    from app.main import app
    from app.models.company import CompanyProfile
    from app.models.industry import IndustryProfile
    from app.models.user import User
    from app.models.work import WorkProfile

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    try:
        with TestClient(app) as tc:
            tc.post(
                "/api/v1/auth/register",
                json={"email": "profiles@example.com", "password": "testpassword123"},
            )
        db: Session = SessionLocal()
        try:
            user = db.query(User).filter(User.email == "profiles@example.com").one()
            assert (
                db.query(CompanyProfile)
                .filter(CompanyProfile.user_id == user.id)
                .count()
                == 1
            )
            assert (
                db.query(WorkProfile).filter(WorkProfile.user_id == user.id).count() == 1
            )
            assert (
                db.query(IndustryProfile)
                .filter(IndustryProfile.user_id == user.id)
                .count()
                == 1
            )
            assert user.team_members == []
        finally:
            db.close()
    finally:
        app.dependency_overrides.clear()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_register_duplicate_email(client: TestClient):
    """Test registration with duplicate email fails."""
    # Register first user
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )
    
    # Try to register with same email
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "anotherpassword"
        }
    )
    
    assert response.status_code == 400
    assert "Email already registered" in response.json()["detail"]


def test_login_valid_credentials(client: TestClient):
    """Test login with valid credentials returns tokens."""
    # Register user first
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )
    
    # Login
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data
    assert data["token_type"] == "bearer"


def test_login_invalid_credentials(client: TestClient):
    """Test login with invalid credentials fails."""
    response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "nonexistent@example.com",
            "password": "wrongpassword"
        }
    )
    
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]


def test_refresh_token(client: TestClient):
    """Test token refresh functionality."""
    # Register and login
    client.post(
        "/api/v1/auth/register",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )
    
    login_response = client.post(
        "/api/v1/auth/login",
        json={
            "email": "test@example.com",
            "password": "testpassword123"
        }
    )
    
    refresh_token = login_response.json()["refresh_token"]
    
    # Refresh token
    response = client.post(
        "/api/v1/auth/refresh",
        json={"refresh_token": refresh_token}
    )
    
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert "refresh_token" in data


def test_logout(client: TestClient):
    """Test logout endpoint."""
    response = client.post("/api/v1/auth/logout")
    
    assert response.status_code == 200
    assert "Successfully logged out" in response.json()["message"]