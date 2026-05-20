"""Add quote_snapshot provenance columns.

Three columns to make snapshot lineage explicit, per plan §1516 and §3023:

- `source`        — how the row got here ('manual', 'scraper', 'partner_feed',
                    'estimated', 'seed'). Backfilled from `offer_details->>source`
                    where present, else 'seed' for existing rows.
- `valid_until`   — date after which the row should be treated as stale.
                    Nullable: NULL means "no explicit expiry, defer to UI".
- `confidence`    — 0.0–1.0 score. 1.0 for human-verified, lower for
                    rating-engine extrapolations. Used by the UI to decide
                    whether to badge a result "Estimated".

These let the API surface freshness honestly (matches plan §3224: "Always
show comparison coverage and freshness date") and let the recommendation
service tag rating-engine outputs without conflating them with verified
snapshots.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005_snapshot_provenance"
down_revision = "0004_add_provider_configs"
branch_labels = None
depends_on = None


VALID_SOURCES = ("manual", "scraper", "partner_feed", "estimated", "seed")


def upgrade() -> None:
    # 1. Add the columns, server-default 'seed' so existing rows are valid
    # immediately and we can drop the default in a follow-up if we ever
    # want INSERTs without a source to fail loudly.
    op.add_column(
        "quote_snapshots",
        sa.Column(
            "source",
            sa.String(length=20),
            nullable=False,
            server_default="seed",
        ),
    )
    op.add_column(
        "quote_snapshots",
        sa.Column("valid_until", sa.Date(), nullable=True),
    )
    op.add_column(
        "quote_snapshots",
        sa.Column(
            "confidence",
            sa.Numeric(precision=4, scale=3),
            nullable=False,
            server_default="1.000",
        ),
    )

    # 2. Backfill `source` from offer_details JSONB if present. Existing
    # seed rows carry {"source": "seed"} already — this preserves that.
    op.execute(
        """
        UPDATE quote_snapshots
        SET source = offer_details->>'source'
        WHERE offer_details ? 'source'
          AND offer_details->>'source' IN ('manual', 'scraper', 'partner_feed', 'estimated', 'seed')
        """
    )

    # 3. CHECK constraint so bad sources can never sneak in via direct SQL.
    op.create_check_constraint(
        "ck_quote_snapshots_source",
        "quote_snapshots",
        "source IN ('manual', 'scraper', 'partner_feed', 'estimated', 'seed')",
    )

    # 4. Confidence bounds.
    op.create_check_constraint(
        "ck_quote_snapshots_confidence_range",
        "quote_snapshots",
        "confidence >= 0 AND confidence <= 1",
    )

    # 5. Index on (provider_id, source) — the worker's snapshot-deactivation
    # pass filters by both, so this keeps it cheap as the table grows.
    op.create_index(
        "ix_quote_snapshots_provider_source",
        "quote_snapshots",
        ["provider_id", "source"],
    )


def downgrade() -> None:
    op.drop_index("ix_quote_snapshots_provider_source", table_name="quote_snapshots")
    op.drop_constraint(
        "ck_quote_snapshots_confidence_range", "quote_snapshots", type_="check"
    )
    op.drop_constraint("ck_quote_snapshots_source", "quote_snapshots", type_="check")
    op.drop_column("quote_snapshots", "confidence")
    op.drop_column("quote_snapshots", "valid_until")
    op.drop_column("quote_snapshots", "source")
