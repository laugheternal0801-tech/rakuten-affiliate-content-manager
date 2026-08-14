from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, HttpUrl
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    """Environment-backed application settings; secret values are never rendered."""

    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    rakuten_application_id: str = ""
    rakuten_access_key: str = ""
    rakuten_affiliate_id: str = ""
    rakuten_api_endpoint: HttpUrl = Field(
        default=HttpUrl("https://openapi.rakuten.co.jp/ichibams/api/IchibaItem/Search/20260701")
    )
    rakuten_api_timeout_seconds: float = Field(default=10.0, ge=1, le=60)
    rakuten_api_cache_ttl_seconds: int = Field(default=900, ge=60, le=86400)
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-5"
    anthropic_api_timeout_seconds: float = Field(default=120.0, ge=5, le=180)
    openai_api_key: str = ""
    openai_image_timeout_seconds: float = Field(default=150.0, ge=10, le=300)
    note_format_playbook_url: HttpUrl = Field(
        default=HttpUrl(
            "https://raw.githubusercontent.com/"
            "laugheternal0801-tech/rakuten-affiliate-content-manager/"
            "note-format-data/data/note_format_playbook.json"
        )
    )
    note_format_playbook_timeout_seconds: float = Field(default=3.0, ge=1, le=10)
    llm_provider: str = "anthropic"
    llm_api_key: str = ""
    database_url: str = f"sqlite:///{(PROJECT_ROOT / 'data' / 'app.db').as_posix()}"

    @property
    def rakuten_configured(self) -> bool:
        return bool(self.rakuten_application_id and self.rakuten_access_key)

    @property
    def llm_configured(self) -> bool:
        return bool(self.claude_api_key)

    @property
    def note_image_generation_configured(self) -> bool:
        return bool(self.openai_api_key)

    @property
    def claude_api_key(self) -> str:
        """Prefer the dedicated Anthropic key while retaining the old LLM setting."""
        if self.anthropic_api_key:
            return self.anthropic_api_key
        if self.llm_provider.lower() in {"anthropic", "claude"}:
            return self.llm_api_key
        return ""


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
