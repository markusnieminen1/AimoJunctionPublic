from pathlib import Path
from typing import Literal

from pydantic import AliasChoices, Field
from pydantic_settings import BaseSettings, SettingsConfigDict

_BACKEND_DIR = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Application settings loaded from environment and .env files."""

    model_config = SettingsConfigDict(
        env_file=(str(_BACKEND_DIR.parent / ".env"), str(_BACKEND_DIR / ".env")),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = Field(
        default="sqlite:///./aimoannos.db",
        validation_alias=AliasChoices("DATABASE_URL", "database_url"),
    )
    jwt_secret: str = Field(
        default="dev-insecure-secret-change-me",
        validation_alias=AliasChoices("JWT_SECRET", "jwt_secret"),
    )
    aimo_base_url: str = Field(
        default="https://api.example.invalid",
        validation_alias=AliasChoices("AIMO_BASE_URL", "aimo_base_url"),
    )
    aimo_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("AIMO_API_KEY", "AimoAPI_key", "aimo_api_key"),
    )
    aimo_mode: Literal["live", "fixture"] = Field(
        default="fixture",
        validation_alias=AliasChoices("AIMO_MODE", "aimo_mode"),
    )
    app_now: str | None = Field(
        default=None,
        validation_alias=AliasChoices("APP_NOW", "app_now"),
    )
    llm_provider: Literal["anthropic", "featherless", "fake"] = Field(
        default="fake",
        validation_alias=AliasChoices("LLM_PROVIDER", "llm_provider"),
    )
    anthropic_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("ANTHROPIC_API_KEY", "anthropic_api_key"),
    )
    featherless_api_key: str = Field(
        default="",
        validation_alias=AliasChoices("FEATHERLESS_API_KEY", "featherless_api_key"),
    )
    llm_model: str = Field(
        default="claude-sonnet-4-5",
        validation_alias=AliasChoices("LLM_MODEL", "llm_model"),
    )
    fast_llm_provider: Literal["featherless", "fake"] = Field(
        default="fake",
        validation_alias=AliasChoices("FAST_LLM_PROVIDER", "fast_llm_provider"),
    )
    fast_llm_model: str = Field(
        default="google/gemma-4-31B-it",
        validation_alias=AliasChoices("FAST_LLM_MODEL", "fast_llm_model"),
    )
    fast_llm_fallback_model: str = Field(
        default="google/gemma-4-26B-A4B-it",
        validation_alias=AliasChoices("FAST_LLM_FALLBACK_MODEL", "fast_llm_fallback_model"),
    )
    approval_hold_hours: float = Field(
        default=5.0,
        validation_alias=AliasChoices("AIMO_HOLD_HOURS", "approval_hold_hours"),
    )
    hold_warning_window_days: int = Field(
        default=7,
        validation_alias=AliasChoices("AIMO_HOLD_WARNING_WINDOW_DAYS", "hold_warning_window_days"),
    )
    hold_warning_suspension_threshold: int = Field(
        default=3,
        validation_alias=AliasChoices(
            "AIMO_HOLD_SUSPENSION_THRESHOLD", "hold_warning_suspension_threshold"
        ),
    )
    agentic_service_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("AGENTIC_SERVICE_URL", "agentic_service_url"),
    )
    telegram_bot_username: str = Field(
        default="your_aimo_bot",
        validation_alias=AliasChoices("TELEGRAM_BOT_USERNAME", "telegram_bot_username"),
    )
    telegram_bot_token: str = Field(
        default="",
        validation_alias=AliasChoices(
            "TELEGRAM_BOT_TOKEN",
            "telegram_api_key",
            "TELEGRAM_API_KEY",
            "telegram_bot_token",
        ),
    )
    llm_max_calls_per_hour: int = Field(
        default=200,
        validation_alias=AliasChoices("LLM_MAX_CALLS_PER_HOUR", "llm_max_calls_per_hour"),
    )
    order_gateway_simulate: Literal["none", "timeout_after_accept", "error"] = Field(
        default="none",
        validation_alias=AliasChoices("ORDER_GATEWAY_SIMULATE", "order_gateway_simulate"),
    )
    app_env: str = Field(
        default="dev",
        validation_alias=AliasChoices("APP_ENV", "app_env"),
    )
    port: int = Field(
        default=8001,
        validation_alias=AliasChoices("PORT", "port"),
    )
    version: str = Field(
        default="0.1.0",
        validation_alias=AliasChoices("VERSION", "version"),
    )


settings = Settings()
