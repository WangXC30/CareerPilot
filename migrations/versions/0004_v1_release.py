"""V1 account, catalogue and evidence contracts."""

import sqlalchemy as sa
from alembic import op

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def owner():
    return sa.ForeignKeyConstraint(["user_id", "tenant_id"], ["users.id", "users.tenant_id"])


def upgrade():
    op.create_table(
        "account_credentials",
        sa.Column("user_id", sa.String(36), primary_key=True),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("email", sa.String(254), nullable=False),
        sa.Column("password_hash", sa.String(200), nullable=False),
        sa.Column("salt", sa.String(64), nullable=False),
        owner(),
        sa.UniqueConstraint("email"),
    )
    op.create_table(
        "login_windows",
        sa.Column("id", sa.String(64), primary_key=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("until", sa.Integer(), nullable=False),
    )
    op.create_table(
        "account_deletions",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("receipt_digest", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("requested_at", sa.String(40), nullable=False),
        sa.Column("completed_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "catalogue_entries",
        sa.Column("id", sa.String(80), primary_key=True),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("payload_json", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("version >= 1"),
    )
    op.create_table(
        "skill_evidence",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("skill_id", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(40), nullable=False),
        sa.Column("source_id", sa.String(36), nullable=False),
        sa.Column("source_version", sa.Integer(), nullable=False),
        sa.Column("conclusion", sa.String(30), nullable=False),
        sa.Column("rubric_version", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("expires_at", sa.String(40), nullable=False),
        owner(),
        sa.UniqueConstraint("id", "user_id", "tenant_id"),
        sa.ForeignKeyConstraint(["skill_id"], ["catalogue_entries.id"]),
        sa.CheckConstraint("kind IN ('self_report','learning_completion','assessment_result')"),
    )
    op.create_index(
        "ix_evidence_owner_skill", "skill_evidence", ["tenant_id", "user_id", "skill_id"]
    )
    op.create_table(
        "evidence_revocations",
        sa.Column("evidence_id", sa.String(36), primary_key=True),
        sa.Column("user_id", sa.String(36), nullable=False),
        sa.Column("tenant_id", sa.String(36), nullable=False),
        sa.Column("reason", sa.String(200), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        owner(),
        sa.ForeignKeyConstraint(
            ["evidence_id", "user_id", "tenant_id"],
            ["skill_evidence.id", "skill_evidence.user_id", "skill_evidence.tenant_id"],
        ),
    )


def downgrade():
    for name in [
        "evidence_revocations",
        "skill_evidence",
        "catalogue_entries",
        "account_deletions",
        "login_windows",
        "account_credentials",
    ]:
        op.drop_table(name)
