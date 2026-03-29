import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class NudgeDismissal(Base):
    """User-dismissed contextual nudge (Requirement 10.x — do not repeat until logic changes)."""

    __tablename__ = "nudge_dismissals"
    __table_args__ = (UniqueConstraint("user_id", "nudge_key", name="uq_nudge_user_key"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    nudge_key = Column(String(160), nullable=False)
    dismissed_at = Column(DateTime(timezone=True), server_default=func.now())

    user = relationship("User", back_populates="nudge_dismissals")
