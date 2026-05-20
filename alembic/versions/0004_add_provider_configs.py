"""Add provider_configs.

Per-provider, per-product configuration row holding band thresholds and
tier definitions as JSON. Used by the recommendation engine to make
pricing/banding adjustments without a code deploy.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0004_add_provider_configs"
down_revision = "0003_add_knowledge_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "provider_configs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id"),
            nullable=False,
        ),
        sa.Column(
            "product_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("products.id"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column("config_json", postgresql.JSONB, nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "provider_id", "product_id", "version", name="uq_provider_configs_pp_version"
        ),
    )
    op.create_index(
        "ix_provider_configs_provider_id", "provider_configs", ["provider_id"]
    )
    op.create_index(
        "ix_provider_configs_product_id", "provider_configs", ["product_id"]
    )
    op.create_index(
        "ix_provider_configs_is_active", "provider_configs", ["is_active"]
    )


def downgrade() -> None:
    op.drop_index("ix_provider_configs_is_active", table_name="provider_configs")
    op.drop_index("ix_provider_configs_product_id", table_name="provider_configs")
    op.drop_index("ix_provider_configs_provider_id", table_name="provider_configs")
    op.drop_table("provider_configs")
