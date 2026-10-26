"""Private V1 aggregates with append-only revision history."""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "growth_records",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("version >= 1"),
        sa.ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
        sa.UniqueConstraint("id", "user_id", "tenant_id"),
    )
    op.create_index("ix_growth_owner_kind", "growth_records", ["tenant_id", "user_id", "kind"])
    op.create_table(
        "growth_revisions",
        sa.Column("record_id", sa.String(36), primary_key=True),
        sa.Column("version", sa.Integer(), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("version >= 1"),
        sa.ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"]),
        sa.ForeignKeyConstraint(
            ["record_id", "user_id", "tenant_id"],
            ["growth_records.id", "growth_records.user_id", "growth_records.tenant_id"],
        ),
    )


def downgrade():
    op.drop_table("growth_revisions")
    op.drop_index("ix_growth_owner_kind", table_name="growth_records")
    op.drop_table("growth_records")
