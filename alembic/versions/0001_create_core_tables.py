from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0001_create_core_tables"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "providers",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
    )
    op.create_index("ix_providers_code", "providers", ["code"], unique=True)

    op.create_table(
        "products",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id"),
            nullable=False,
        ),
        sa.Column("code", sa.String(length=50), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("product_line", sa.String(length=50), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
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
    )
    op.create_unique_constraint(
        "uq_products_provider_code", "products", ["provider_id", "code"]
    )
    op.create_index("ix_products_provider_id", "products", ["provider_id"])

    op.create_table(
        "quote_requests",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("product_line", sa.String(length=50), nullable=False),
        sa.Column("country", sa.String(length=2), nullable=False),
        sa.Column("raw_inquiry", postgresql.JSONB, nullable=False),
        sa.Column("normalized_bands", postgresql.JSONB, nullable=False),
        sa.Column("coverage_preference", sa.String(length=20), nullable=False),
        sa.Column("driver_age", sa.Integer(), nullable=False),
        sa.Column("years_licensed", sa.Integer(), nullable=False),
        sa.Column("vehicle_value_eur", sa.Integer(), nullable=False),
        sa.Column("vehicle_power_kw", sa.Integer(), nullable=False),
        sa.Column("vehicle_age_years", sa.Integer(), nullable=False),
        sa.Column("annual_mileage_km", sa.Integer(), nullable=False),
        sa.Column("claims_last_5y", sa.Integer(), nullable=False),
        sa.Column("usage_type", sa.String(length=20), nullable=False),
        sa.Column("age_band", sa.String(length=20), nullable=False),
        sa.Column("vehicle_value_band", sa.String(length=20), nullable=False),
        sa.Column("mileage_band", sa.String(length=20), nullable=False),
        sa.Column("claims_band", sa.String(length=10), nullable=False),
        sa.Column("power_band", sa.String(length=20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )

    op.create_table(
        "quote_snapshots",
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
        sa.Column("product_line", sa.String(length=50), nullable=False),
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        sa.Column("coverage_level", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("annual_premium_cents", sa.Integer(), nullable=False),
        sa.Column("deductible_cents", sa.Integer(), nullable=False),
        sa.Column("age_band", sa.String(length=20), nullable=False),
        sa.Column("vehicle_value_band", sa.String(length=20), nullable=False),
        sa.Column("mileage_band", sa.String(length=20), nullable=False),
        sa.Column("claims_band", sa.String(length=10), nullable=False),
        sa.Column("power_band", sa.String(length=20), nullable=False),
        sa.Column("usage_type", sa.String(length=20), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("offer_details", postgresql.JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )
    op.create_index(
        "ix_quote_snapshots_provider_product",
        "quote_snapshots",
        ["provider_id", "product_id"],
    )
    op.create_index(
        "ix_quote_snapshots_bands",
        "quote_snapshots",
        [
            "age_band",
            "vehicle_value_band",
            "mileage_band",
            "claims_band",
            "power_band",
            "usage_type",
        ],
    )

    op.create_table(
        "recommendations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "quote_request_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quote_requests.id"),
            nullable=False,
        ),
        sa.Column(
            "provider_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("providers.id"),
            nullable=False,
        ),
        sa.Column(
            "best_match_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quote_snapshots.id"),
            nullable=True,
        ),
        sa.Column(
            "lower_cost_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quote_snapshots.id"),
            nullable=True,
        ),
        sa.Column(
            "higher_cover_snapshot_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("quote_snapshots.id"),
            nullable=True,
        ),
        sa.Column(
            "all_matching_snapshot_ids",
            postgresql.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("ranking_version", sa.String(length=20), nullable=False, server_default="v1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("recommendations")
    op.drop_index("ix_quote_snapshots_bands", table_name="quote_snapshots")
    op.drop_index("ix_quote_snapshots_provider_product", table_name="quote_snapshots")
    op.drop_table("quote_snapshots")
    op.drop_table("quote_requests")
    op.drop_index("ix_products_provider_id", table_name="products")
    op.drop_constraint("uq_products_provider_code", "products", type_="unique")
    op.drop_table("products")
    op.drop_index("ix_providers_code", table_name="providers")
    op.drop_table("providers")
