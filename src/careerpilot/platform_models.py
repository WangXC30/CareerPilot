"""V0 platform records. Private tables always bind user and tenant ownership."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from careerpilot.persistence import Base, new_id, utc_now


def ownership():
    return ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"])


class RoleGrant(Base):
    __tablename__ = "role_grants"
    __table_args__ = (ownership(), CheckConstraint("role IN ('student','operator','admin')"))
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    role: Mapped[str] = mapped_column(String(16), default="student")


class Consent(Base):
    __tablename__ = "consents"
    __table_args__ = (ownership(),)
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    purpose: Mapped[str] = mapped_column(String(80), primary_key=True)
    granted: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class StateRecord(Base):
    __tablename__ = "state_records"
    __table_args__ = (
        ownership(),
        Index("ix_state_owner", "tenant_id", "user_id"),
        CheckConstraint("version >= 1"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(40))
    version: Mapped[int] = mapped_column(Integer, default=1)


class StateChange(Base):
    __tablename__ = "state_changes"
    __table_args__ = (ownership(),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    actor: Mapped[str] = mapped_column(String(40))
    previous: Mapped[str] = mapped_column(String(40))
    current: Mapped[str] = mapped_column(String(40))
    reason: Mapped[str] = mapped_column(String(200))
    revision: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class BackgroundTask(Base):
    __tablename__ = "background_tasks"
    __table_args__ = (
        ownership(),
        Index("ix_task_due", "status", "available_at"),
        CheckConstraint("attempts >= 0 AND max_attempts > 0"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(40), default="queued")
    version: Mapped[int] = mapped_column(Integer, default=1)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    available_at: Mapped[int] = mapped_column(Integer, default=0)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    lease_token: Mapped[str] = mapped_column(String(36), default="")
    checkpoint: Mapped[str] = mapped_column(String(80), default="queued")
    last_error_class: Mapped[str] = mapped_column(String(80), default="")
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class OutboxEvent(Base):
    __tablename__ = "outbox_events"
    __table_args__ = (ownership(),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    aggregate_id: Mapped[str] = mapped_column(String(36))
    event_type: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[int] = mapped_column(Integer, default=1)
    aggregate_version: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    delivered: Mapped[int] = mapped_column(Integer, default=0)
    occurred_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class InboxReceipt(Base):
    __tablename__ = "inbox_receipts"
    event_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    consumer: Mapped[str] = mapped_column(String(80), primary_key=True)
    processed_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class SourceRecord(Base):
    """Public operational metadata only. No job/resume content in this table."""

    __tablename__ = "source_records"
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    access_basis: Mapped[str] = mapped_column(String(200))
    scopes_json: Mapped[str] = mapped_column(Text, default="[]")
    rate_per_minute: Mapped[int] = mapped_column(Integer, default=0)
    retention_days: Mapped[int] = mapped_column(Integer, default=0)
    reviewed_by: Mapped[str] = mapped_column(String(80), default="")
    reviewed_until: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(40), default="disabled")
    last_error_class: Mapped[str] = mapped_column(String(80), default="")
    retries: Mapped[int] = mapped_column(Integer, default=0)
    responsibility: Mapped[str] = mapped_column(String(80), default="market")
    last_checked_at: Mapped[str] = mapped_column(String(40), default="")


class ModelUsage(Base):
    __tablename__ = "model_usage"
    __table_args__ = (ownership(), Index("ix_model_quota", "tenant_id", "user_id", "period"))
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    task_id: Mapped[str] = mapped_column(String(36))
    period: Mapped[str] = mapped_column(String(10))
    model_id: Mapped[str] = mapped_column(String(80))
    prompt_version: Mapped[str] = mapped_column(String(80))
    schema_version: Mapped[str] = mapped_column(String(80))
    reserved_tokens: Mapped[int] = mapped_column(Integer)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    estimated_cost_micros: Mapped[int] = mapped_column(Integer, default=0)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error_class: Mapped[str] = mapped_column(String(80), default="pending")
    trace_id: Mapped[str] = mapped_column(String(36))


class QuotaBucket(Base):
    __tablename__ = "quota_buckets"
    __table_args__ = (ownership(), CheckConstraint("used >= 0"))
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    scope: Mapped[str] = mapped_column(String(80), primary_key=True)
    used: Mapped[int] = mapped_column(Integer, default=0)


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (ownership(),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    action: Mapped[str] = mapped_column(String(80))
    target: Mapped[str] = mapped_column(String(200))
    payload_hash: Mapped[str] = mapped_column(String(64))
    expires_at: Mapped[int] = mapped_column(Integer)
    consumed: Mapped[int] = mapped_column(Integer, default=0)


class RequestMetric(Base):
    __tablename__ = "request_metrics"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    tenant_id: Mapped[str] = mapped_column(String(36), default="")
    route: Mapped[str] = mapped_column(String(200))
    status: Mapped[int] = mapped_column(Integer)
    elapsed_ms: Mapped[int] = mapped_column(Integer)
    trace_id: Mapped[str] = mapped_column(String(36))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
