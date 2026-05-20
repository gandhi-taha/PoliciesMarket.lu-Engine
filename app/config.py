from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.providers.loader import load_providers


class ProviderRuntimeConfig(BaseModel):
    code: str
    name: str
    enabled: bool = True
    product_lines: list[str] = Field(default_factory=list)
    # Adapter selection. "snapshot" reads from the local quote_snapshots
    # table; "live" calls an external feed. Adapters register against
    # (code, mode) pairs in app.providers.registry.
    mode: str = "snapshot"
    # Live-feed settings — ignored when mode == "snapshot".
    base_url: str | None = None
    api_key: str | None = None
    timeout_seconds: float = 5.0


def _providers_from_yaml() -> dict[str, ProviderRuntimeConfig]:
    """Build the settings.providers dict from providers.yaml.

    Falls back to a minimal Foyer-only roster if the YAML can't be
    read — keeps the test suite working when run from outside the
    engine root.
    """

    entries = load_providers()
    if not entries:
        return {
            "foyer": ProviderRuntimeConfig(
                code="foyer",
                name="Foyer Assurances",
                enabled=True,
                product_lines=["car"],
            )
        }
    return {
        entry.code: ProviderRuntimeConfig(
            code=entry.code,
            name=entry.name,
            enabled=entry.enabled,
            product_lines=[entry.product_line],
        )
        for entry in entries
    }


class SearchRuntimeConfig(BaseModel):
    top_k_default: int = 5
    rrf_k: int = 60
    embedding_dimension: int = 384
    cache_ttl_seconds: int = 300


class Settings(BaseSettings):
    app_name: str = "policiesmarket-engine"
    app_env: str = "local"
    debug: bool = False
    api_prefix: str = "/v1"
    database_url: str = (
        "postgresql+psycopg://postgres:postgres@localhost:5432/policiesmarket"
    )
    mistral_api_key: str | None = None
    mistral_model: str = "mistral-large-latest"
    mistral_base_url: str = "https://api.mistral.ai"
    mistral_timeout_seconds: float = 30.0
    # v1 provider roster is loaded from `providers.yaml` at the engine
    # root. Single source of truth: add an insurer = add a YAML entry.
    # See `app/providers/loader.py` for the loader and the YAML schema.
    providers: dict[str, ProviderRuntimeConfig] = Field(
        default_factory=_providers_from_yaml
    )
    search: SearchRuntimeConfig = Field(default_factory=SearchRuntimeConfig)
    cache_enabled: bool = True
    observability_enabled: bool = True

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
