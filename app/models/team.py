from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Float, Enum
from sqlalchemy.dialects.postgresql import UUID, JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from app.db.database import Base


class ReportingPreference(str, enum.Enum):
    """Enum for reporting preferences."""
    DATA = "data"
    EMOTIONAL = "emotional"
    CONCISE = "concise"


class TagSource(str, enum.Enum):
    """Enum for behavior tag sources."""
    AI = "ai"
    MANUAL = "manual"


class TeamMember(Base):
    """Team member model for Layer 1 - Know Your Team."""
    
    __tablename__ = "team_members"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    role = Column(String, nullable=False)
    personality_traits = Column(Text, nullable=True)
    communication_preference = Column(String, nullable=True)
    decision_style = Column(String, nullable=True)
    reporting_preference = Column(Enum(ReportingPreference), nullable=True)
    taboo = Column(Text, nullable=True)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship to user
    user = relationship("User", back_populates="team_members")
    
    # Relationship to behavior tags
    behavior_tags = relationship("BehaviorTag", back_populates="team_member", cascade="all, delete-orphan")


class BehaviorTag(Base):
    """Behavior tag model for AI-generated behavioral insights."""
    
    __tablename__ = "behavior_tags"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    team_member_id = Column(UUID(as_uuid=True), ForeignKey("team_members.id"), nullable=False, index=True)
    label = Column(String, nullable=False)
    category = Column(String, nullable=False)
    confidence = Column(Float, nullable=False)  # 0.0 to 1.0
    source = Column(Enum(TagSource), nullable=False, default=TagSource.AI)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship to team member
    team_member = relationship("TeamMember", back_populates="behavior_tags")