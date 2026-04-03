"""Add llm_usage_logs table for persistent LLM cost tracking.

Revision ID: add_llm_usage_001
Revises: add_analytics_001
"""

from alembic import op
import sqlalchemy as sa

# revision identifiers
revision = "add_llm_usage_001"
down_revision = "add_analytics_001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "llm_usage_logs",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("model", sa.String(128), nullable=False),
        sa.Column("operation", sa.String(32), nullable=False),
        sa.Column("prompt_tokens", sa.Integer, default=0, nullable=False),
        sa.Column("completion_tokens", sa.Integer, default=0, nullable=False),
        sa.Column("total_tokens", sa.Integer, default=0, nullable=False),
        sa.Column("cache_read_tokens", sa.Integer, default=0, nullable=False),
        sa.Column("cache_write_tokens", sa.Integer, default=0, nullable=False),
        sa.Column("estimated_cost_usd", sa.Float, default=0.0, nullable=False),
        sa.Column("cache_savings_usd", sa.Float, default=0.0, nullable=False),
        sa.Column("channel_id", sa.String, nullable=True),
        sa.Column("created_at", sa.DateTime, nullable=False, server_default=sa.func.now()),
    )
    op.create_index("ix_llm_usage_logs_operation", "llm_usage_logs", ["operation"])
    op.create_index("ix_llm_usage_logs_created_at", "llm_usage_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_llm_usage_logs_created_at", table_name="llm_usage_logs")
    op.drop_index("ix_llm_usage_logs_operation", table_name="llm_usage_logs")
    op.drop_table("llm_usage_logs")
