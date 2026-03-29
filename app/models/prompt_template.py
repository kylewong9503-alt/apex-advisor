import uuid
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class SavedPromptTemplate(Base):
    """User-saved prompt template with versioning (Requirements 6.5, 6.7)."""

    __tablename__ = "saved_prompt_templates"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    name = Column(String, nullable=False)
    scene = Column(String, nullable=False)
    body = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1)
    # When user last saved or marked template in sync with profiles (stale if profiles newer)
    last_synced_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="saved_prompt_templates")
