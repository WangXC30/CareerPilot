"""Replayable UI events. Private payloads have the same owner as their run."""

from sqlalchemy import ForeignKeyConstraint, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerpilot.persistence import Base, utc_now
from careerpilot.platform_models import ownership


class WorkflowEvent(Base):
    __tablename__ = "workflow_events"
    __table_args__ = (
        ownership(),
        ForeignKeyConstraint(
            ["run_id", "user_id", "tenant_id"],
            ["growth_records.id", "growth_records.user_id", "growth_records.tenant_id"],
        ),
        UniqueConstraint("run_id", "event_key"),
    )
    run_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    event_key: Mapped[str] = mapped_column(String(100))
    event_type: Mapped[str] = mapped_column(String(40))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
