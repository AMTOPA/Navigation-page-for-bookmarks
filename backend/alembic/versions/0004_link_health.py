"""Add link health checks and newest-job index.

Revision ID: 0004
Revises: 0003
"""

from alembic import op
import sqlalchemy as sa


revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())
    if "link_health" not in tables:
        op.create_table(
            "link_health",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("bookmark_id", sa.String(36), sa.ForeignKey("bookmarks.id"), nullable=False, unique=True),
            sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
            sa.Column("http_status", sa.Integer()),
            sa.Column("final_url", sa.Text(), nullable=False, server_default=""),
            sa.Column("latency_ms", sa.Integer()),
            sa.Column("error", sa.Text(), nullable=False, server_default=""),
            sa.Column("checked_at", sa.DateTime(timezone=True)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_link_health_bookmark_id", "link_health", ["bookmark_id"], unique=True)
        op.create_index("ix_link_health_status", "link_health", ["status"])
        op.create_index("ix_link_health_checked_at", "link_health", ["checked_at"])
        op.create_index("ix_link_health_status_checked", "link_health", ["status", "checked_at"])

    inspector = sa.inspect(bind)
    job_indexes = {item["name"] for item in inspector.get_indexes("jobs")}
    if "ix_jobs_created_at" not in job_indexes:
        op.create_index("ix_jobs_created_at", "jobs", ["created_at"])


def downgrade():
    op.drop_index("ix_jobs_created_at", table_name="jobs")
    op.drop_table("link_health")
