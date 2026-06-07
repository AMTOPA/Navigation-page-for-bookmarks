"""Phase two models and persistent search.

Revision ID: 0002
Revises: 0001
"""

from alembic import op
import sqlalchemy as sa


revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    def has_table(name):
        return name in inspector.get_table_names()

    def has_column(table, column):
        return column in {item["name"] for item in inspector.get_columns(table)}

    if not has_column("bookmarks", "favicon_cache_url"):
        op.add_column("bookmarks", sa.Column("favicon_cache_url", sa.Text(), nullable=False, server_default=""))
    if not has_column("bookmarks", "image_cache_url"):
        op.add_column("bookmarks", sa.Column("image_cache_url", sa.Text(), nullable=False, server_default=""))
    if not has_column("admin_users", "updated_at"):
        op.add_column("admin_users", sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))
    if not has_table("job_batches"):
        op.create_table(
        "job_batches",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("job_type", sa.String(30), nullable=False, server_default=""),
        sa.Column("created_by", sa.String(100), nullable=False, server_default=""),
        sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True)),
        )
    if not has_column("jobs", "batch_id"):
        # SQLite cannot add a foreign-key constraint without rebuilding the table.
        # The application still validates batch ids, and fresh databases get the FK
        # from SQLAlchemy metadata in the initial migration.
        op.add_column("jobs", sa.Column("batch_id", sa.String(36)))
        op.create_index("ix_jobs_batch_id", "jobs", ["batch_id"])
    if not has_table("ai_providers"):
        op.create_table(
        "ai_providers",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False, unique=True),
        sa.Column("base_url", sa.Text(), nullable=False),
        sa.Column("encrypted_api_key", sa.Text(), nullable=False, server_default=""),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not has_table("ai_models"):
        op.create_table(
        "ai_models",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("provider_id", sa.String(36), sa.ForeignKey("ai_providers.id"), nullable=False),
        sa.Column("display_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("model_name", sa.String(200), nullable=False),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("provider_id", "model_name", name="uq_provider_model"),
        )
    if not has_table("ai_routes"):
        op.create_table(
        "ai_routes",
        sa.Column("task_type", sa.String(40), primary_key=True),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ai_models.id")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if not has_table("search_indexes"):
        op.create_table(
        "search_indexes",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("bookmark_id", sa.String(36), sa.ForeignKey("bookmarks.id"), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ai_models.id"), nullable=False),
        sa.Column("text_hash", sa.String(64), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("index_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("bookmark_id", "model_id", "index_version", name="uq_search_index_version"),
        )
    if not has_table("search_query_cache"):
        op.create_table(
        "search_query_cache",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("query_hash", sa.String(64), nullable=False),
        sa.Column("model_id", sa.String(36), sa.ForeignKey("ai_models.id"), nullable=False),
        sa.Column("vector", sa.LargeBinary(), nullable=False),
        sa.Column("dimensions", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("query_hash", "model_id", name="uq_query_model"),
        )
    if not has_table("media_assets"):
        op.create_table(
        "media_assets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("source_url", sa.Text(), nullable=False, unique=True),
        sa.Column("source_hash", sa.String(64), nullable=False),
        sa.Column("content_hash", sa.String(64), nullable=False, server_default=""),
        sa.Column("local_path", sa.Text(), nullable=False, server_default=""),
        sa.Column("public_url", sa.Text(), nullable=False, server_default=""),
        sa.Column("media_type", sa.String(100), nullable=False, server_default=""),
        sa.Column("status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("retry_after", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        )
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql(
            "CREATE VIRTUAL TABLE IF NOT EXISTS bookmark_fts USING fts5("
            "bookmark_id UNINDEXED, title, url, category, tags, description, summary, keywords, notes)"
        )


def downgrade():
    bind = op.get_bind()
    if bind.dialect.name == "sqlite":
        bind.exec_driver_sql("DROP TABLE IF EXISTS bookmark_fts")
    op.drop_table("media_assets")
    op.drop_table("search_query_cache")
    op.drop_table("search_indexes")
    op.drop_table("ai_routes")
    op.drop_table("ai_models")
    op.drop_table("ai_providers")
    op.drop_index("ix_jobs_batch_id", table_name="jobs")
    op.drop_column("jobs", "batch_id")
    op.drop_table("job_batches")
    op.drop_column("admin_users", "updated_at")
    op.drop_column("bookmarks", "image_cache_url")
    op.drop_column("bookmarks", "favicon_cache_url")
