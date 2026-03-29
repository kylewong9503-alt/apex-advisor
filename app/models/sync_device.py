import uuid

from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.db.database import Base


class SyncDevice(Base):
    """Registered client device for cross-device sync (Requirement 8.2, 8.4)."""

    __tablename__ = "sync_devices"
    __table_args__ = (UniqueConstraint("user_id", "device_id", name="uq_sync_device_user_device"),)

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4, index=True)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=False, index=True)
    device_id = Column(String(64), nullable=False)
    label = Column(String(255), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())
    last_ack_revision = Column(String(128), nullable=True)

    user = relationship("User", back_populates="sync_devices")
