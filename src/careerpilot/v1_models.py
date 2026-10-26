"""Typed security, catalogue and evidence tables for the V1 growth release."""

from sqlalchemy import CheckConstraint, ForeignKeyConstraint, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from careerpilot.persistence import Base, new_id, utc_now
from careerpilot.platform_models import ownership


class Credential(Base):
    __tablename__ = "account_credentials"
    __table_args__ = (ownership(), UniqueConstraint("email"))
    user_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(36))
    email: Mapped[str] = mapped_column(String(254))
    password_hash: Mapped[str] = mapped_column(String(200))
    salt: Mapped[str] = mapped_column(String(64))


class LoginWindow(Base):
    __tablename__ = "login_windows"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    attempts: Mapped[int] = mapped_column(default=0)
    until: Mapped[int] = mapped_column(default=0)


class DeletionRequest(Base):
    __tablename__ = "account_deletions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    receipt_digest: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    requested_at: Mapped[str] = mapped_column(String(40), default=utc_now)
    completed_at: Mapped[str] = mapped_column(String(40), default="")


class CatalogueEntry(Base):
    __tablename__ = "catalogue_entries"
    __table_args__ = (CheckConstraint("version >= 1"),)
    id: Mapped[str] = mapped_column(String(80), primary_key=True)
    kind: Mapped[str] = mapped_column(String(20))
    version: Mapped[int] = mapped_column(default=1)
    payload_json: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[str] = mapped_column(String(40), default=utc_now)


class SkillEvidence(Base):
    __tablename__ = "skill_evidence"
    __table_args__ = (
        ownership(),
        UniqueConstraint("id", "user_id", "tenant_id"),
        ForeignKeyConstraint(["skill_id"], ["catalogue_entries.id"]),
        Index("ix_evidence_owner_skill", "tenant_id", "user_id", "skill_id"),
        CheckConstraint("kind IN ('self_report','learning_completion','assessment_result')"),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    skill_id: Mapped[str] = mapped_column(String(80))
    kind: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(36))
    source_version: Mapped[int] = mapped_column()
    conclusion: Mapped[str] = mapped_column(String(30))
    rubric_version: Mapped[str] = mapped_column(String(80), default="")
    scope: Mapped[str] = mapped_column(String(80), default="foundation")
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
    expires_at: Mapped[str] = mapped_column(String(40))


class EvidenceRevocation(Base):
    __tablename__ = "evidence_revocations"
    __table_args__ = (
        ownership(),
        ForeignKeyConstraint(
            ["evidence_id", "user_id", "tenant_id"],
            ["skill_evidence.id", "skill_evidence.user_id", "skill_evidence.tenant_id"],
        ),
    )
    evidence_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(36))
    tenant_id: Mapped[str] = mapped_column(String(36))
    reason: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[str] = mapped_column(String(40), default=utc_now)
