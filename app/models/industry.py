import enum
import uuid
from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class IndustrySourceType(str, enum.Enum):
    """Source types for industry raw inputs (Industry_Input)."""

    RESEARCH = "research"  # 研报
    DATA = "data"  # 行业数据
    DYNAMICS = "dynamics"  # 行业动态
    COMPETITOR = "competitor"  # 竞品资讯
    REGULATORY = "regulatory"  # 监管文件
    OTHER = "other"


class ConflictFlag(str, enum.Enum):
    """Relationship to existing intelligence (Requirement 5.6)."""

    NEW = "new"
    SUPPLEMENT = "supplement"
    CONFLICT = "conflict"


class IndustryDigestStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"
    SUPERSEDED = "superseded"
    MERGED = "merged"


class ReminderFrequency(str, enum.Enum):
    WEEKLY = "weekly"
    BIWEEKLY = "biweekly"
    MONTHLY = "monthly"


# Four dimensions for Industry_Intelligence (Requirement 5.2)
INDUSTRY_INTELLIGENCE_DIMENSIONS = (
    "industry_trends",
    "competitive_landscape",
    "regulatory_dynamics",
    "data_highlights",
)


class IndustryProfile(Base):
    """Industry profile model for Layer 4 - Know Your Industry."""

    __tablename__ = "industry_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="industry_profile")

    industry_inputs = relationship(
        "IndustryInput", back_populates="industry_profile", cascade="all, delete-orphan"
    )
    industry_digests = relationship(
        "IndustryDigest", back_populates="industry_profile", cascade="all, delete-orphan"
    )
    industry_items = relationship(
        "IndustryItem", back_populates="industry_profile", cascade="all, delete-orphan"
    )
    topic_materials = relationship(
        "TopicMaterial", back_populates="industry_profile", cascade="all, delete-orphan"
    )
    update_reminders = relationship(
        "IndustryUpdateReminder", back_populates="industry_profile", cascade="all, delete-orphan"
    )


class IndustryInput(Base):
    """Raw industry material fragment (Industry_Input)."""

    __tablename__ = "industry_inputs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    industry_profile_id = Column(UUID(as_uuid=True), ForeignKey("industry_profiles.id"), nullable=False, index=True)
    raw_text = Column(Text, nullable=False)
    source_type = Column(
        Enum(IndustrySourceType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    industry_profile = relationship("IndustryProfile", back_populates="industry_inputs")


class IndustryDigest(Base):
    """AI-generated digest before user confirmation (Industry_Digest)."""

    __tablename__ = "industry_digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    industry_profile_id = Column(UUID(as_uuid=True), ForeignKey("industry_profiles.id"), nullable=False, index=True)
    status = Column(
        Enum(IndustryDigestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=IndustryDigestStatus.PENDING_REVIEW,
    )
    dimension_summaries = Column(JSON, nullable=False, default=dict)
    dimension_merge_modes = Column(JSON, nullable=False, default=dict)
    # Per-dimension annotation: new | supplement | conflict
    dimension_annotations = Column(JSON, nullable=False, default=dict)
    conflicts = Column(JSON, nullable=False, default=list)
    source_input_ids = Column(JSON, nullable=False, default=list)
    # Draft topic rows to create on confirm
    topic_materials_preview = Column(JSON, nullable=False, default=list)
    # Optional structured card for data_highlights (keyed by dimension)
    data_cards = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    industry_profile = relationship("IndustryProfile", back_populates="industry_digests")


class IndustryItem(Base):
    """Persisted insight in Industry_Intelligence knowledge base."""

    __tablename__ = "industry_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    industry_profile_id = Column(UUID(as_uuid=True), ForeignKey("industry_profiles.id"), nullable=False, index=True)
    dimension = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(
        Enum(IndustrySourceType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    conflict_flag = Column(
        Enum(ConflictFlag, values_callable=lambda x: [e.value for e in x]),
        nullable=True,
    )
    industry_input_id = Column(
        UUID(as_uuid=True), ForeignKey("industry_inputs.id"), nullable=True, index=True
    )
    # Structured card for data_highlights (Requirement 5.12): value, source, as_of
    data_card = Column(JSON, nullable=True)
    highlight_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    industry_profile = relationship("IndustryProfile", back_populates="industry_items")
    source_input = relationship("IndustryInput", foreign_keys=[industry_input_id])


class TopicMaterial(Base):
    """Conversation topic library entry (Topic_Material)."""

    __tablename__ = "topic_materials"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    industry_profile_id = Column(UUID(as_uuid=True), ForeignKey("industry_profiles.id"), nullable=False, index=True)
    title = Column(String, nullable=False)
    core_insight = Column(Text, nullable=False)
    data_support = Column(Text, nullable=False)
    tags = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    industry_profile = relationship("IndustryProfile", back_populates="topic_materials")


class IndustryUpdateReminder(Base):
    """Per-dimension update reminder (Requirement 5.13)."""

    __tablename__ = "industry_update_reminders"
    __table_args__ = (
        UniqueConstraint(
            "industry_profile_id",
            "dimension",
            name="uq_industry_reminder_dimension",
        ),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    industry_profile_id = Column(UUID(as_uuid=True), ForeignKey("industry_profiles.id"), nullable=False, index=True)
    dimension = Column(String, nullable=False)
    frequency = Column(
        Enum(ReminderFrequency, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    next_due_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    industry_profile = relationship("IndustryProfile", back_populates="update_reminders")
