"""Pydantic shapes for the DB-driven provider configuration.

`config_json` on a `ProviderConfig` row is unstructured at the database
layer (JSONB) but we validate it through `ProviderConfigPayload` on read
and write so an operator can't store something that the engine can't
consume.
"""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BandThreshold(BaseModel):
    """One band: an inclusive upper bound + a label.

    The recommendation engine walks the list in order and picks the first
    threshold whose `max` is >= the input value. `max=None` means the
    catch-all "everything above". Use exactly one `max=None` per list and
    put it last.
    """

    max: int | None = None
    label: str


class BandConfig(BaseModel):
    age: list[BandThreshold] = Field(default_factory=list)
    vehicle_value: list[BandThreshold] = Field(default_factory=list)
    mileage: list[BandThreshold] = Field(default_factory=list)
    claims: list[BandThreshold] = Field(default_factory=list)
    power: list[BandThreshold] = Field(default_factory=list)

    @model_validator(mode="after")
    def _validate_catch_alls(self) -> "BandConfig":
        for name in ("age", "vehicle_value", "mileage", "claims", "power"):
            bands = getattr(self, name)
            if not bands:
                continue
            catch_all_indices = [i for i, b in enumerate(bands) if b.max is None]
            if len(catch_all_indices) != 1:
                raise ValueError(
                    f"Band '{name}' must contain exactly one threshold with max=None"
                )
            if catch_all_indices[0] != len(bands) - 1:
                raise ValueError(
                    f"Band '{name}' catch-all threshold (max=None) must be last"
                )
        return self


class TierConfig(BaseModel):
    """Optional pricing/coverage tier metadata used by the explainer.

    The engine doesn't price quotes from this — pricing still comes from
    `quote_snapshots` (or the live feed). This is metadata so the
    explainer can describe which coverage level a tier maps to.
    """

    code: str
    name: str
    description: str | None = None
    coverage_level: str


class ProviderConfigPayload(BaseModel):
    """Top-level config JSON shape stored in `provider_configs.config_json`."""

    bands: BandConfig = Field(default_factory=BandConfig)
    tiers: list[TierConfig] = Field(default_factory=list)
    # Free-form bag for forward-compat additions that don't deserve a
    # schema change yet.
    extras: dict[str, object] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")


class ProviderConfigOut(BaseModel):
    """Returned by the admin endpoints."""

    id: UUID
    provider_id: UUID
    product_id: UUID
    provider_code: str
    product_code: str
    version: int
    is_active: bool
    config: ProviderConfigPayload
    notes: str | None
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ProviderConfigUpsert(BaseModel):
    """Request body for the admin upsert endpoint.

    Posting this creates a new `version` for the (provider_code, product_code)
    pair, deactivates older versions, and returns the new row.
    """

    provider_code: str
    product_code: str
    config: ProviderConfigPayload
    notes: str | None = None

    model_config = ConfigDict(extra="forbid")
