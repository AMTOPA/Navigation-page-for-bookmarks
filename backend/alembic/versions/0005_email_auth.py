"""email auth codes

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-22
"""

from alembic import op
import sqlalchemy as sa


revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "email_verification_codes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("email", sa.String(length=254), nullable=False),
        sa.Column("purpose", sa.String(length=30), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column("ip", sa.String(length=64), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_email_verification_codes_email", "email_verification_codes", ["email"])
    op.create_index("ix_email_verification_codes_ip", "email_verification_codes", ["ip"])
    op.create_index("ix_email_verification_codes_purpose", "email_verification_codes", ["purpose"])
    op.create_index("ix_email_verification_codes_expires_at", "email_verification_codes", ["expires_at"])
    op.create_index("ix_email_verification_codes_used_at", "email_verification_codes", ["used_at"])
    op.create_index(
        "ix_email_codes_email_purpose_created",
        "email_verification_codes",
        ["email", "purpose", "created_at"],
    )
    op.create_index(
        "ix_email_codes_ip_purpose_created",
        "email_verification_codes",
        ["ip", "purpose", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_email_codes_ip_purpose_created", table_name="email_verification_codes")
    op.drop_index("ix_email_codes_email_purpose_created", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_used_at", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_expires_at", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_purpose", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_ip", table_name="email_verification_codes")
    op.drop_index("ix_email_verification_codes_email", table_name="email_verification_codes")
    op.drop_table("email_verification_codes")
