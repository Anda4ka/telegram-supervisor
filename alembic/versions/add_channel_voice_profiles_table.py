"""Add channel_voice_profiles table for Brand Voice Engine.

Revision ID: add_voice_001
Revises: add_llm_usage_001
"""

from alembic import op
import sqlalchemy as sa

revision = "add_voice_001"
down_revision = "add_llm_usage_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "channel_voice_profiles",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("channel_id", sa.BigInteger, nullable=False),
        sa.Column("preset_name", sa.String(32), nullable=False, server_default="default"),
        sa.Column("profile_data", sa.JSON, nullable=False, server_default="{}"),
        sa.Column("analyzed_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("channel_id", "preset_name", name="uq_voice_channel_preset"),
    )
    op.create_index("ix_voice_channel_id", "channel_voice_profiles", ["channel_id"])


def downgrade() -> None:
    op.drop_index("ix_voice_channel_id", table_name="channel_voice_profiles")
    op.drop_table("channel_voice_profiles")
