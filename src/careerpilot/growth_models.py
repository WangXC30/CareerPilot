"""V1 private aggregates and immutable revision records."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerpilot.persistence import Base, new_id, utc_now
from careerpilot.platform_models import ownership


class GrowthRecord(Base):
    __tablename__ = "growth_records"
    __table_args__ = (
        ownership(),
        UniqueConstraint("id", "user_id", "tenant_id"),
        CheckConstraint("version >= 1"),
        Index("ix_growth_owner_kind", "tenant_id", "user_id", "kind"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    kind: Mapped[str] = mapped_column(String(32))
    version: Mapped[int] = mapped_column(default=1)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class GrowthRevision(Base):
    __tablename__ = "growth_revisions"
    __table_args__ = (
        ownership(),
        ForeignKeyConstraint(
            ["record_id", "user_id", "tenant_id"],
            ["growth_records.id", "growth_records.user_id", "growth_records.tenant_id"],
        ),
        CheckConstraint("version >= 1"),
    )
    record_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    version: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
