"""SQLAlchemy ORM models for PostgreSQL metadata storage."""

from sqlalchemy import String, Integer, Float, Boolean, DateTime, ForeignKey, Text, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.sql import func
from datetime import datetime, timezone
import uuid
from typing import Optional, List

from app.db.postgres import Base


def generate_uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    """User account model."""
    
    __tablename__ = "users"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    google_sub: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False, default="user")  # "admin" | "user"
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")  # "active" | "pending" | "blocked"
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    last_login: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    
    collections: Mapped[List["Collection"]] = relationship(back_populates="user", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_users_google_sub", "google_sub"),
        Index("ix_users_email", "email"),
    )


class Collection(Base):
    """Collection model - represents a document collection owned by a user."""
    
    __tablename__ = "collections"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    folder_path: Mapped[str] = mapped_column(String(511), nullable=False)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="active")  # "active" | "ingesting" | "error" | "archived"
    doc_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    
    user: Mapped["User"] = relationship(back_populates="collections")
    ingest_jobs: Mapped[List["IngestJob"]] = relationship(back_populates="collection", cascade="all, delete-orphan")
    
    __table_args__ = (
        Index("ix_collections_user_id", "user_id"),
    )


class IngestJob(Base):
    """Ingest job tracking model."""
    
    __tablename__ = "ingest_jobs"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    collection_id: Mapped[str] = mapped_column(String(36), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(50), nullable=False, default="pending")  # "pending" | "running" | "completed" | "failed" | "cancelled"
    progress: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    total_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    processed_docs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    completed_at: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    options: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON blob
    last_completed_file: Mapped[Optional[str]] = mapped_column(String(511), nullable=True)
    
    collection: Mapped["Collection"] = relationship(back_populates="ingest_jobs")


class RevokedToken(Base):
    """Revoked JWT token blocklist."""
    
    __tablename__ = "revoked_tokens"
    
    jti: Mapped[str] = mapped_column(String(36), primary_key=True)
    revoked_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    expires_at: Mapped[int] = mapped_column(Integer, nullable=False)


class DriveWatchChannel(Base):
    """Google Drive push notification channel registration."""
    
    __tablename__ = "drive_watch_channels"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    channel_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    resource_id: Mapped[str] = mapped_column(String(255), nullable=False)
    collection_id: Mapped[str] = mapped_column(String(36), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    folder_id: Mapped[str] = mapped_column(String(255), nullable=False)
    access_token: Mapped[str] = mapped_column(String(511), nullable=False)
    expiry_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))


class Ontology(Base):
    """Ontology schema for a collection."""
    
    __tablename__ = "ontologies"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    collection_id: Mapped[str] = mapped_column(String(36), ForeignKey("collections.id", ondelete="CASCADE"), unique=True, nullable=False, index=True)
    version: Mapped[str] = mapped_column(String(50), nullable=False, default="1.0")
    schema_json: Mapped[str] = mapped_column(Text, nullable=False)  # JSON schema
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    updated_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))


class UserFeedback(Base):
    """User feedback on graph entities/relationships."""
    
    __tablename__ = "user_feedback"
    
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    collection_id: Mapped[str] = mapped_column(String(36), ForeignKey("collections.id", ondelete="CASCADE"), nullable=False, index=True)
    entity_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "node" | "edge"
    entity_id: Mapped[str] = mapped_column(String(36), nullable=False, index=True)
    feedback_type: Mapped[str] = mapped_column(String(50), nullable=False)  # "approve" | "reject" | "edit"
    previous_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    new_value: Mapped[Optional[str]] = mapped_column(Text, nullable=True)  # JSON
    created_at: Mapped[int] = mapped_column(Integer, nullable=False, default=lambda: int(datetime.now(timezone.utc).timestamp() * 1000))
    
    __table_args__ = (
        Index("ix_feedback_entity", "entity_type", "entity_id"),
    )
