"""Persistence baseline. Schema changes are exclusively Alembic migrations."""

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast
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
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

SCHEMA_REVISION = "0005"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid4())


def database_url() -> str:
    direct = os.environ.get("CAREERPILOT_DATABASE_URL")
    path = os.environ.get("CAREERPILOT_DATABASE_URL_FILE")
    candidate = os.environ.get("CAREERPILOT_DEPLOYMENT_MODE") == "candidate"
    if direct and path:
        raise ValueError("Configure exactly one database source")
    if candidate and not path:
        raise ValueError("Candidate database configuration requires a secret file")
    if path:
        try:
            with Path(path).open("r", encoding="utf-8") as stream:
                raw = stream.read(8193)
            value = raw.strip()
            if not value or len(raw) > 8192 or "\n" in value or "\r" in value:
                raise ValueError
            if candidate and not value.startswith("postgresql+psycopg://"):
                raise ValueError
            return value
        except (OSError, ValueError):
            raise ValueError("Database secret file is unavailable or invalid") from None
    return direct or "sqlite:///./data/careerpilot.db"


def make_engine(url: str):
    connection: dict[str, Any] = {"check_same_thread": False} if url.startswith("sqlite") else {}
    options: dict[str, Any] = {}
    if url.startswith("postgresql"):
        connection = {"connect_timeout": 3}
        if os.environ.get("CAREERPILOT_DEPLOYMENT_MODE") == "candidate":
            connection.update(options="-c statement_timeout=5000", tcp_user_timeout=3000)
            options = {"pool_size": 5, "max_overflow": 5, "pool_timeout": 3}
    engine = create_engine(url, connect_args=connection, pool_pre_ping=True, **options)
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
    device_label: Mapped[str] = mapped_column(String(120), default="未知设备")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
    risk_notice: Mapped[str] = mapped_column(String(200), default="")


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


def affected(result) -> int:
    return cast(CursorResult, result).rowcount
