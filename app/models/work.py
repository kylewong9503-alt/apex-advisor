from sqlalchemy import Column, String, DateTime, ForeignKey, Text, Enum, Float, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
import uuid
import enum
from app.db.database import Base


class ProjectStatus(str, enum.Enum):
    """Enum for project status."""
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    PAUSED = "paused"


class WorkstreamStatus(str, enum.Enum):
    """Enum for workstream status."""
    IN_PROGRESS = "in-progress"
    COMPLETED = "completed"
    PAUSED = "paused"


class FollowUpStatus(str, enum.Enum):
    """Enum for follow-up item status."""
    PENDING = "pending"
    COMPLETED = "completed"


class DimensionStatus(str, enum.Enum):
    """Enum for review dimension status."""
    COMPLIANT = "compliant"
    NON_COMPLIANT = "non-compliant"


class ComplianceTrend(str, enum.Enum):
    """Enum for compliance trend."""
    IMPROVING = "improving"
    DECLINING = "declining"
    STABLE = "stable"


class WorkProfile(Base):
    """Work profile model for Layer 3 - Know Your Work."""
    
    __tablename__ = "work_profiles"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship to user
    user = relationship("User", back_populates="work_profile")
    
    # Relationships
    deliverable_standards = relationship("DeliverableStandard", back_populates="work_profile", cascade="all, delete-orphan")
    projects = relationship("Project", back_populates="work_profile", cascade="all, delete-orphan")
    meeting_notes = relationship("MeetingNote", back_populates="work_profile", cascade="all, delete-orphan")
    workstreams = relationship("Workstream", back_populates="work_profile", cascade="all, delete-orphan")
    follow_up_items = relationship("FollowUpItem", back_populates="work_profile", cascade="all, delete-orphan")


class DeliverableStandard(Base):
    """Deliverable standard model for work scene requirements."""
    
    __tablename__ = "deliverable_standards"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    work_profile_id = Column(UUID(as_uuid=True), ForeignKey("work_profiles.id"), nullable=False, index=True)
    work_scene = Column(String, nullable=False)  # Required field
    format_requirements = Column(Text, nullable=True)
    style_requirements = Column(Text, nullable=True)
    thinking_framework = Column(Text, nullable=True)
    perspective_and_stance = Column(Text, nullable=True)
    habit_preferences = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    work_profile = relationship("WorkProfile", back_populates="deliverable_standards")
    review_histories = relationship("ReviewHistory", back_populates="deliverable_standard", cascade="all, delete-orphan")
    deliverable_patterns = relationship("DeliverablePattern", back_populates="deliverable_standard", cascade="all, delete-orphan")


class ReviewHistory(Base):
    """Review history model for tracking deliverable evaluations."""
    
    __tablename__ = "review_histories"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    deliverable_standard_id = Column(UUID(as_uuid=True), ForeignKey("deliverable_standards.id"), nullable=False, index=True)
    reviewed_at = Column(DateTime(timezone=True), server_default=func.now())
    overall_compliance = Column(Float, nullable=False)  # 0.0 to 1.0
    
    # Relationships
    deliverable_standard = relationship("DeliverableStandard", back_populates="review_histories")
    dimension_reviews = relationship("DimensionReview", back_populates="review_history", cascade="all, delete-orphan")


class DimensionReview(Base):
    """Dimension review model for individual standard dimensions."""
    
    __tablename__ = "dimension_reviews"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    review_history_id = Column(UUID(as_uuid=True), ForeignKey("review_histories.id"), nullable=False, index=True)
    dimension = Column(String, nullable=False)  # format, style, framework, perspective, habit
    status = Column(
        Enum(DimensionStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    details = Column(Text, nullable=False)
    suggestions = Column(Text, nullable=True)
    
    # Relationship
    review_history = relationship("ReviewHistory", back_populates="dimension_reviews")


class DeliverablePattern(Base):
    """Deliverable pattern model for trend analysis."""
    
    __tablename__ = "deliverable_patterns"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    deliverable_standard_id = Column(UUID(as_uuid=True), ForeignKey("deliverable_standards.id"), nullable=False, index=True)
    based_on_count = Column(Integer, nullable=False)
    persistent_issues = Column(Text, nullable=True)  # JSON array as text
    improving_dimensions = Column(Text, nullable=True)  # JSON array as text
    compliance_trend = Column(
        Enum(ComplianceTrend, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    trend_description = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship
    deliverable_standard = relationship("DeliverableStandard", back_populates="deliverable_patterns")


class Project(Base):
    """Project model for work tracking."""
    
    __tablename__ = "projects"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    work_profile_id = Column(UUID(as_uuid=True), ForeignKey("work_profiles.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    status = Column(
        Enum(ProjectStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=ProjectStatus.IN_PROGRESS,
    )
    deadline = Column(DateTime(timezone=True), nullable=True)
    description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationship
    work_profile = relationship("WorkProfile", back_populates="projects")


class MeetingNote(Base):
    """Meeting note model for meeting documentation."""
    
    __tablename__ = "meeting_notes"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    work_profile_id = Column(UUID(as_uuid=True), ForeignKey("work_profiles.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    meeting_date = Column(DateTime(timezone=True), nullable=False)
    participants = Column(String, nullable=True)
    content = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    work_profile = relationship("WorkProfile", back_populates="meeting_notes")
    follow_up_items = relationship("FollowUpItem", back_populates="meeting_note")


class Workstream(Base):
    """Workstream model for ongoing work tracking."""
    
    __tablename__ = "workstreams"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    work_profile_id = Column(UUID(as_uuid=True), ForeignKey("work_profiles.id"), nullable=False, index=True)
    name = Column(String, nullable=False)  # Required field
    progress_description = Column(Text, nullable=True)
    goals_and_requirements = Column(Text, nullable=True)
    owner = Column(String, nullable=True)
    status = Column(
        Enum(WorkstreamStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=WorkstreamStatus.IN_PROGRESS,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    work_profile = relationship("WorkProfile", back_populates="workstreams")
    follow_up_items = relationship("FollowUpItem", back_populates="workstream")


class FollowUpItem(Base):
    """Follow-up item model for action tracking."""
    
    __tablename__ = "follow_up_items"
    
    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    work_profile_id = Column(UUID(as_uuid=True), ForeignKey("work_profiles.id"), nullable=False, index=True)
    meeting_note_id = Column(UUID(as_uuid=True), ForeignKey("meeting_notes.id"), nullable=True, index=True)
    workstream_id = Column(UUID(as_uuid=True), ForeignKey("workstreams.id"), nullable=True, index=True)
    description = Column(Text, nullable=False)
    owner = Column(String, nullable=True)
    due_date = Column(DateTime(timezone=True), nullable=True)
    status = Column(
        Enum(FollowUpStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=FollowUpStatus.PENDING,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    
    # Relationships
    work_profile = relationship("WorkProfile", back_populates="follow_up_items")
    meeting_note = relationship("MeetingNote", back_populates="follow_up_items")
    workstream = relationship("Workstream", back_populates="follow_up_items")