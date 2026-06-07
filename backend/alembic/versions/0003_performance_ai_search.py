"""Performance indexes and persistent AI search cache.

Revision ID: 0003
Revises: 0002
"""

from alembic import op
import sqlalchemy as sa


revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def _index_names(inspector, table):
    return {item["name"] for item in inspector.get_indexes(table)}


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "ai_search_cache" not in tables:
        op.create_table(
            "ai_search_cache",
            sa.Column("id", sa.String(36), primary_key=True),
            sa.Column("cache_key", sa.String(64), nullable=False, unique=True),
            sa.Column("normalized_query", sa.Text(), nullable=False),
            sa.Column("group_filter", sa.Text(), nullable=False, server_default=""),
            sa.Column("model_id", sa.String(36), sa.ForeignKey("ai_models.id"), nullable=False),
            sa.Column("embedding_model_id", sa.String(36), sa.ForeignKey("ai_models.id")),
            sa.Column("index_revision", sa.String(100), nullable=False, server_default=""),
            sa.Column("answer", sa.Text(), nullable=False, server_default=""),
            sa.Column("suggestions", sa.JSON(), nullable=False),
            sa.Column("result_ids", sa.JSON(), nullable=False),
            sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_ai_search_cache_cache_key", "ai_search_cache", ["cache_key"], unique=True)
        op.create_index("ix_ai_search_cache_model_id", "ai_search_cache", ["model_id"])
        op.create_index("ix_ai_search_cache_embedding_model_id", "ai_search_cache", ["embedding_model_id"])
        op.create_index("ix_ai_search_cache_index_revision", "ai_search_cache", ["index_revision"])
        op.create_index("ix_ai_search_cache_expires_at", "ai_search_cache", ["expires_at"])

    inspector = sa.inspect(bind)
    for table, name, columns in (
        ("bookmarks", "ix_bookmarks_deleted_updated", ["deleted_at", "updated_at"]),
        ("jobs", "ix_jobs_status_created", ["status", "created_at"]),
        ("jobs", "ix_jobs_type_created", ["job_type", "created_at"]),
        ("audit_logs", "ix_audit_logs_type_created", ["event_type", "created_at"]),
        ("audit_logs", "ix_audit_logs_level_created", ["level", "created_at"]),
    ):
        if name not in _index_names(inspector, table):
            op.create_index(name, table, columns)


def downgrade():
    for table, name in (
        ("audit_logs", "ix_audit_logs_level_created"),
        ("audit_logs", "ix_audit_logs_type_created"),
        ("jobs", "ix_jobs_type_created"),
        ("jobs", "ix_jobs_status_created"),
        ("bookmarks", "ix_bookmarks_deleted_updated"),
    ):
        op.drop_index(name, table_name=table)
    op.drop_table("ai_search_cache")
