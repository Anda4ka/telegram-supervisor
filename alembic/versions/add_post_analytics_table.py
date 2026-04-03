"""Add post_analytics table for channel metrics collection.

Revision ID: add_analytics_001
Revises: (add to chain manually after checking current head)
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "add_analytics_001"
down_revision = "c4d5e6f7a8b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "post_analytics",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("channel_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("message_id", sa.BigInteger, nullable=False, index=True),
        sa.Column("views", sa.Integer, nullable=False, default=0),
        sa.Column("forwards", sa.Integer, nullable=False, default=0),
        sa.Column("reactions_count", sa.Integer, nullable=False, default=0),
        sa.Column("reactions_breakdown", sa.JSON, nullable=True),
        sa.Column("comments_count", sa.Integer, nullable=False, default=0),
        sa.Column("published_at", sa.DateTime, nullable=False),
        sa.Column("hours_since_publish", sa.Float, nullable=False),
        sa.Column("measured_at", sa.DateTime, nullable=False),
    )

    # Index for fast lookups: latest metric per message
    op.create_index(
        "ix_post_analytics_channel_message_measured",
        "post_analytics",
        ["channel_id", "message_id", "measured_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_post_analytics_channel_message_measured")
    op.drop_table("post_analytics")
