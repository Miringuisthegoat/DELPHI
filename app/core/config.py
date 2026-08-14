"""
Centralized configuration for FPL Oracle AI.

All environment-dependent values are declared here and nowhere else.
Services should import `settings` from this module rather than reading
os.environ directly, so that configuration stays testable and swappable
(e.g. SQLite -> PostgreSQL) without touching business logic.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application-wide settings, loaded from environment variables / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- App ---
    app_name: str = Field(default="FPL Oracle AI")
    app_env: str = Field(default="development")
    debug: bool = Field(default=True)
    log_level: str = Field(default="INFO")

    # --- Database ---
    database_url: str = Field(default="sqlite:///./data/fpl_oracle.db")

    # --- FPL API ---
    fpl_base_url: str = Field(default="https://fantasy.premierleague.com/api")
    fpl_request_timeout_seconds: int = Field(default=15)
    fpl_max_retries: int = Field(default=3)

    # --- Pipeline security ---
    pipeline_secret: str | None = Field(default=None)
    """Shared secret required in the X-Pipeline-Secret header to trigger
    POST /api/v1/pipeline/run/{gameweek}. None means the check is skipped -
    fine for local dev, but this MUST be set before deploying publicly."""

    # --- My squad ---
    fpl_team_id: int | None = Field(default=None)

    # --- Telegram delivery ---
    telegram_bot_token: str | None = Field(default=None)
    telegram_chat_id: str | None = Field(default=None)

    @field_validator("fpl_team_id", mode="before")
    @classmethod
    def _blank_team_id_is_none(cls, value: object) -> object:
        """Treat an empty FPL_TEAM_ID env value (not yet set) as unset rather than an error."""
        if value in ("", None):
            return None
        return value

    # --- Scheduler ---
    enable_scheduler: bool = Field(default=False)
    weekly_update_cron: str = Field(default="0 8 * * 2")

    # --- Paths ---
    data_dir: Path = Field(default=Path("./data"))
    log_dir: Path = Field(default=Path("./logs"))

    # --- Phase 5: DELPHI prediction engine ---
    ml_model_dir: Path = Field(
        default=Path("./data/processed/models"),
        description="Where trained model artifacts (joblib) and metadata are persisted.",
    )
    ml_min_samples_for_training: int = Field(
        default=200,
        description=(
            "Minimum (player, gameweek) training rows required before "
            "DELPHI will train/trust a Random Forest model. Below this "
            "(e.g. preseason, or the first few gameweeks of a new season) "
            "the engine falls back to the transparent heuristic predictor."
        ),
    )
    ml_rf_n_estimators: int = Field(default=300)
    ml_rf_max_depth: int | None = Field(default=10)
    ml_rf_min_samples_leaf: int = Field(default=3)
    ml_random_state: int = Field(default=42)
    ml_default_horizons: tuple[int, ...] = Field(default=(1, 3, 5))
    ml_model_name: str = Field(
        default="delphi",
        description="Base identifier stored on Prediction.model_name (suffixed with _heuristic or _rf).",
    )
    ml_model_version: str = Field(default="1.0.0")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_directories(self) -> None:
        """Create data/log directories if they don't already exist."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        (self.data_dir / "raw").mkdir(parents=True, exist_ok=True)
        (self.data_dir / "processed").mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.ml_model_dir.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return a cached Settings instance (singleton for the process)."""
    settings = Settings()
    settings.ensure_directories()
    return settings


settings = get_settings()
