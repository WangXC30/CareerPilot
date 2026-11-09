"""Append replayable workflow events; keep all existing records and IDs."""

import sqlalchemy as sa
from alembic import op

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "workflow_events",
        sa.Column("run_id", sa.String(36), primary_key=True),
        sa.Column("sequence", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("event_key", sa.String(100), nullable=False),
        sa.Column("event_type", sa.String(40), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["run_id", "user_id", "tenant_id"],
            ["growth_records.id", "growth_records.user_id", "growth_records.tenant_id"],
        ),
        sa.UniqueConstraint("run_id", "event_key"),
    )


def downgrade():
    op.drop_table("workflow_events")
