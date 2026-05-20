"""Admin endpoints for editing the DB-driven provider configuration.

Mounted under `/admin/providers`. These are intentionally not behind the
public `/v1` prefix — the assumption is that the gateway / Cloud Run
ingress restricts `/admin/*` to operators.

Routes
------
- GET  /admin/providers/configs                          → list all configs
- GET  /admin/providers/configs/{provider}/{product}     → active config for a pair
- PUT  /admin/providers/configs                          → upsert (creates new version)
- POST /admin/providers/snapshots/import                 → bulk-import quote_snapshots from CSV
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.schemas.provider_config import (
    ProviderConfigOut,
    ProviderConfigUpsert,
)
from app.services import provider_config as cfg_service
from app.services.snapshot_import import (
    CsvImportError,
    CsvImportResult,
    import_snapshots_csv,
)

router = APIRouter(prefix="/admin/providers", tags=["admin"])


class SnapshotImportResponse(BaseModel):
    inserted: int
    deactivated: int
    providers_touched: list[str]
    rows_seen: int

    @classmethod
    def from_result(cls, result: CsvImportResult) -> "SnapshotImportResponse":
        return cls(
            inserted=result.inserted,
            deactivated=result.deactivated,
            providers_touched=result.providers_touched,
            rows_seen=result.rows_seen,
        )


@router.get("/configs", response_model=list[ProviderConfigOut])
def list_configs(db: Session = Depends(get_db)) -> list[ProviderConfigOut]:
    return cfg_service.list_configs(db)


@router.get(
    "/configs/{provider_code}/{product_code}", response_model=ProviderConfigOut
)
def get_active_config(
    provider_code: str, product_code: str, db: Session = Depends(get_db)
) -> ProviderConfigOut:
    out = cfg_service.get_active(db, provider_code, product_code)
    if out is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                f"No active config for provider={provider_code!r} "
                f"product={product_code!r}"
            ),
        )
    return out


@router.put("/configs", response_model=ProviderConfigOut, status_code=status.HTTP_201_CREATED)
def upsert_config(
    payload: ProviderConfigUpsert, db: Session = Depends(get_db)
) -> ProviderConfigOut:
    try:
        out = cfg_service.upsert_config(db, payload)
    except LookupError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    db.commit()
    return out


@router.post(
    "/snapshots/import",
    response_model=SnapshotImportResponse,
    status_code=status.HTTP_201_CREATED,
)
async def import_snapshots(
    file: UploadFile = File(
        ..., description="CSV per the schema in app/services/snapshot_import.py"
    ),
    db: Session = Depends(get_db),
) -> SnapshotImportResponse:
    """Bulk-load operator-collected quotes (Layer A pipe).

    Each row becomes a `quote_snapshot` with `source='manual'`. Rows at
    a colliding (provider, product, bands, usage, coverage, snapshot_date)
    key are marked inactive so re-uploading a corrected CSV is safe.
    Returns counts on success; a single malformed row aborts the whole
    import with HTTP 400 (the import is atomic).
    """

    raw = await file.read()
    try:
        csv_text = raw.decode("utf-8-sig")  # handles Excel BOM
    except UnicodeDecodeError as exc:
        raise HTTPException(
            status_code=400, detail="CSV must be UTF-8 encoded."
        ) from exc

    try:
        result = import_snapshots_csv(db, csv_text)
    except CsvImportError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    db.commit()
    return SnapshotImportResponse.from_result(result)
