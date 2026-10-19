"""Persistence baseline. Schema changes are exclusively Alembic migrations."""

import os
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


def database_url() -> str:
    return os.environ.get("CAREERPILOT_DATABASE_URL", "sqlite:///./data/careerpilot.db")


def make_engine(url: str):
    engine = create_engine(
        url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {}
    )
    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def enable_foreign_keys(connection, _):
            connection.execute("PRAGMA foreign_keys=ON")

    return engine


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"
    __table_args__ = (UniqueConstraint("id", "tenant_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), nullable=False)
    display_name: Mapped[str] = mapped_column(String(100))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class SessionToken(Base):
    __tablename__ = "session_tokens"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
    )
    digest: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    expires_at: Mapped[int] = mapped_column(Integer)


class CareerGoal(Base):
    __tablename__ = "career_goals"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
        CheckConstraint("status IN ('draft', 'active', 'archived')"),
        CheckConstraint("version >= 1"),
        Index("ix_goal_owner", "tenant_id", "user_id"),
        Index(
            "uq_active_goal",
            "tenant_id",
            "user_id",
            unique=True,
            sqlite_where=text("status = 'active'"),
            postgresql_where=text("status = 'active'"),
        ),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    title: Mapped[str] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(16), default="draft")
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    revision: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    __table_args__ = (
        ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
    )
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_hash: Mapped[str] = mapped_column(String(64))
    response_json: Mapped[str] = mapped_column(Text)
