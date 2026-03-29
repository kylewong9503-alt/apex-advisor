from sqlalchemy import Column, String, DateTime, Boolean
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
from app.db.database import Base


class User(Base):
    """User model for authentication and profile management."""
    
    __tablename__ = "users"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    password_hash = Column(String, nullable=False)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_login_at = Column(DateTime(timezone=True), nullable=True)
    
    # Relationships to four-layer profiles
    team_members = relationship("TeamMember", back_populates="user", cascade="all, delete-orphan")
    company_profile = relationship("CompanyProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    work_profile = relationship("WorkProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    industry_profile = relationship("IndustryProfile", back_populates="user", uselist=False, cascade="all, delete-orphan")
    saved_prompt_templates = relationship(
        "SavedPromptTemplate",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    chat_sessions = relationship(
        "ChatSession",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    nudge_dismissals = relationship(
        "NudgeDismissal",
        back_populates="user",
        cascade="all, delete-orphan",
    )
    sync_devices = relationship(
        "SyncDevice",
        back_populates="user",
        cascade="all, delete-orphan",
    )