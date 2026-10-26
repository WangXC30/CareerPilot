"""Device descriptions and conservative login notices; no IP or fingerprint storage."""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "session_tokens",
        sa.Column("device_label", sa.String(120), nullable=False, server_default="未知设备"),
    )
    op.add_column(
        "session_tokens", sa.Column("created_at", sa.String(40), nullable=False, server_default="")
    )
    op.add_column(
        "session_tokens",
        sa.Column("risk_notice", sa.String(200), nullable=False, server_default=""),
    )


def downgrade():
    with op.batch_alter_table("session_tokens") as batch:
        batch.drop_column("risk_notice")
        batch.drop_column("created_at")
        batch.drop_column("device_label")
