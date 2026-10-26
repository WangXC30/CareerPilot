"""Identity, goal isolation, immutable audit entries and request deduplication."""

import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def ownership():
    return sa.ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"])


def upgrade():
    op.create_table(
        "users",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.UniqueConstraint("id", "tenant_id"),
    )
    op.create_table(
        "session_tokens",
        sa.Column("digest", sa.String(64), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("expires_at", sa.Integer, nullable=False),
        ownership(),
    )
    op.create_table(
        "career_goals",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("version", sa.Integer, nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        ownership(),
        sa.CheckConstraint("status IN ('draft', 'active', 'archived')"),
        sa.CheckConstraint("version >= 1"),
    )
    op.create_index("ix_goal_owner", "career_goals", ["tenant_id", "user_id"])
    op.create_index(
        "uq_active_goal",
        "career_goals",
        ["tenant_id", "user_id"],
        unique=True,
        sqlite_where=sa.text("status = 'active'"),
        postgresql_where=sa.text("status = 'active'"),
    )
    op.create_table(
        "audit_events",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("aggregate_id", sa.String(36), nullable=False),
        sa.Column("action", sa.String(80), nullable=False),
        sa.Column("revision", sa.Integer, nullable=False),
        sa.Column("trace_id", sa.String(36), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        ownership(),
    )
    op.create_table(
        "idempotency_records",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), primary_key=True),
        sa.Column("key", sa.String(128), primary_key=True),
        sa.Column("request_hash", sa.String(64), nullable=False),
        sa.Column("response_json", sa.Text, nullable=False),
        ownership(),
    )


def downgrade():
    for table in ("idempotency_records", "audit_events", "career_goals", "session_tokens", "users"):
        op.drop_table(table)
