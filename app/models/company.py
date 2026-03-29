import enum
import uuid
from sqlalchemy import Column, DateTime, Enum, ForeignKey, JSON, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class SourceType(str, enum.Enum):
    """Enum for intelligence input source types."""

    MEETING = "meeting"
    NOTICE = "notice"
    SPEECH = "speech"
    FINANCIAL = "financial"
    NEWS = "news"
    OTHER = "other"


class DigestStatus(str, enum.Enum):
    """Lifecycle of an Intelligence_Digest before/after merge."""

    PENDING_REVIEW = "pending_review"
    SUPERSEDED = "superseded"
    MERGED = "merged"


# Four dimensions for Company_Intelligence (Requirements 3.2)
COMPANY_INTELLIGENCE_DIMENSIONS = (
    "business_performance",
    "strategic_direction",
    "cultural_signals",
    "internal_rules",
)


class CompanyProfile(Base):
    """Company profile model for Layer 2 - Know Your Company."""

    __tablename__ = "company_profiles"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="company_profile")

    intelligence_items = relationship(
        "IntelligenceItem", back_populates="company_profile", cascade="all, delete-orphan"
    )
    intelligence_inputs = relationship(
        "IntelligenceInput", back_populates="company_profile", cascade="all, delete-orphan"
    )
    intelligence_digests = relationship(
        "IntelligenceDigest", back_populates="company_profile", cascade="all, delete-orphan"
    )
    stakeholder_notes = relationship(
        "StakeholderNote", back_populates="company_profile", cascade="all, delete-orphan"
    )


class IntelligenceInput(Base):
    """Raw company intelligence fragment pasted by the user (Intelligence_Input)."""

    __tablename__ = "intelligence_inputs"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    company_profile_id = Column(UUID(as_uuid=True), ForeignKey("company_profiles.id"), nullable=False, index=True)
    raw_text = Column(Text, nullable=False)
    source_type = Column(
        Enum(SourceType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    company_profile = relationship("CompanyProfile", back_populates="intelligence_inputs")


class IntelligenceDigest(Base):
    """AI-generated structured digest before user confirmation (Intelligence_Digest)."""

    __tablename__ = "intelligence_digests"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    company_profile_id = Column(UUID(as_uuid=True), ForeignKey("company_profiles.id"), nullable=False, index=True)
    status = Column(
        Enum(DigestStatus, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
        default=DigestStatus.PENDING_REVIEW,
    )
    # Per-dimension summary text (editable by user before confirm)
    dimension_summaries = Column(JSON, nullable=False, default=dict)
    # Suggested merge mode per dimension: supplement | override
    dimension_merge_modes = Column(JSON, nullable=False, default=dict)
    # Conflicts or tension with existing knowledge (Requirement 3.6)
    conflicts = Column(JSON, nullable=False, default=list)
    # Source Intelligence_Input ids (UUID strings) included in this run
    source_input_ids = Column(JSON, nullable=False, default=list)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company_profile = relationship("CompanyProfile", back_populates="intelligence_digests")


class IntelligenceItem(Base):
    """Persisted insight row in the Company_Intelligence knowledge base."""

    __tablename__ = "intelligence_items"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    company_profile_id = Column(UUID(as_uuid=True), ForeignKey("company_profiles.id"), nullable=False, index=True)
    dimension = Column(String, nullable=False)
    content = Column(Text, nullable=False)
    source_type = Column(
        Enum(SourceType, values_callable=lambda x: [e.value for e in x]),
        nullable=False,
    )
    intelligence_input_id = Column(
        UUID(as_uuid=True), ForeignKey("intelligence_inputs.id"), nullable=True, index=True
    )
    # Shown in "实时洞察" banner until this time (Requirement 3.5 / 24h)
    highlight_until = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company_profile = relationship("CompanyProfile", back_populates="intelligence_items")
    source_input = relationship("IntelligenceInput", foreign_keys=[intelligence_input_id])


class StakeholderNote(Base):
    """Stakeholder note for key personnel (Stakeholder_Note)."""

    __tablename__ = "stakeholder_notes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    company_profile_id = Column(UUID(as_uuid=True), ForeignKey("company_profiles.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    department = Column(String, nullable=True)
    influence_description = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    company_profile = relationship("CompanyProfile", back_populates="stakeholder_notes")
