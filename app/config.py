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
    openai_model: str = "gpt-5.6-terra"
    openai_image_timeout_seconds: float = Field(default=150.0, ge=10, le=300)
    gemini_api_key: str = ""
    google_api_key: str = ""
    gemini_model: str = "gemini-3.7-flash"
    ai_council_openai_model: str = "gpt-5.6-sol"
    ai_council_anthropic_model: str = "claude-fable-5-1"
    ai_council_gemini_model: str = "gemini-3.1-pro-preview"
    ai_council_timeout_seconds: float = Field(default=900.0, ge=30, le=3600)
    ai_council_max_output_tokens: int = Field(default=16_000, ge=256, le=16_000)
    ai_os_external_api_enabled: bool = False
    ai_os_mock_mode: bool = True
    ai_os_daily_budget_usd: float = Field(default=1.0, ge=0, le=10_000)
    ai_os_max_run_cost_usd: float = Field(default=0.25, ge=0, le=10_000)
    ai_os_max_model_calls_per_run: int = Field(default=5, ge=0, le=100)
    ai_os_max_search_calls_per_run: int = Field(default=1, ge=0, le=100)
    ai_os_llm_cache_ttl_seconds: int = Field(default=86400, ge=0, le=2592000)
    ai_provider_cost_reserves_usd: dict[str, float] = Field(
        default_factory=lambda: {
            "demo": 0.0,
            "openai": 0.03,
            "anthropic": 0.04,
            "gemini": 0.01,
        }
    )
    ai_provider_quality: dict[str, float] = Field(
        default_factory=lambda: {
            "demo": 0.35,
            "openai": 0.88,
            "anthropic": 0.90,
            "gemini": 0.82,
        }
    )
    ai_provider_latency_ms: dict[str, int] = Field(
        default_factory=lambda: {
            "demo": 10,
            "openai": 5000,
            "anthropic": 5500,
            "gemini": 3500,
        }
    )
    ai_provider_context_tokens: dict[str, int] = Field(
        default_factory=lambda: {
            "demo": 2000000,
            "openai": 200000,
            "anthropic": 200000,
            "gemini": 1000000,
        }
    )
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
    x_bearer_token: str = ""
    reddit_client_id: str = ""
    reddit_client_secret: str = ""
    reddit_user_agent: str = "sns-market-intelligence/0.1"
    youtube_api_key: str = ""
    tiktok_api_key: str = ""
    meta_access_token: str = ""
    pinterest_access_token: str = ""
    market_intelligence_manual_import_dir: str = ""
    market_intelligence_web_feed_urls: str = ""
    market_intelligence_timeout_seconds: float = Field(default=30.0, ge=3, le=180)
    market_intelligence_max_concurrency: int = Field(default=5, ge=1, le=20)
    market_intelligence_max_retries: int = Field(default=2, ge=0, le=5)
    market_intelligence_cache_ttl_seconds: int = Field(default=3600, ge=0, le=86400)
    market_intelligence_platform_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "x": 1.0,
            "reddit": 0.8,
            "youtube": 1.0,
            "tiktok": 1.0,
            "instagram": 1.0,
            "pinterest": 0.7,
            "web": 1.0,
        }
    )
    market_intelligence_confidence_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "evidence_volume": 0.20,
            "source_diversity": 0.15,
            "platform_diversity": 0.15,
            "temporal_consistency": 0.10,
            "model_agreement": 0.15,
            "data_quality": 0.20,
            "counter_evidence": 0.05,
        }
    )
    creative_asset_dir: str = ""
    creative_external_api_enabled: bool = False
    creative_default_quality_level: str = "standard"
    creative_default_budget_usd: float = Field(default=0.0, ge=0)
    creative_max_revision_rounds: int = Field(default=2, ge=0, le=5)
    creative_candidates_draft: int = Field(default=2, ge=1, le=12)
    creative_candidates_standard: int = Field(default=3, ge=1, le=12)
    creative_candidates_premium: int = Field(default=4, ge=1, le=24)
    creative_candidates_flagship: int = Field(default=6, ge=1, le=24)
    creative_openai_image_model: str = ""
    creative_google_image_model: str = ""
    creative_google_video_model: str = ""
    creative_xai_api_key: str = ""
    creative_xai_model: str = ""
    creative_flux_api_key: str = ""
    creative_flux_model: str = ""
    creative_runway_api_key: str = ""
    creative_runway_model: str = ""
    creative_text_role_models: dict[str, dict[str, str]] = Field(default_factory=dict)
    publishing_enabled: bool = False
    publishing_external_api_enabled: bool = False
    publishing_dry_run: bool = True
    publishing_autonomy: str = "assisted"
    publishing_default_timezone: str = "Asia/Tokyo"
    publishing_max_retries: int = Field(default=3, ge=0, le=10)
    publishing_retry_base_seconds: int = Field(default=30, ge=1, le=3600)
    publishing_max_schedule_lateness_minutes: int = Field(default=15, ge=0, le=1440)
    publishing_duplicate_window_days: int = Field(default=30, ge=1, le=365)
    publishing_exploration_ratio: float = Field(default=0.2, ge=0, le=1)
    publishing_performance_windows: str = "1h,6h,24h,72h,7d,30d"
    publishing_allow_remote_delete: bool = False
    publishing_worker_poll_seconds: int = Field(default=15, ge=1, le=3600)
    publishing_worker_lease_seconds: int = Field(default=120, ge=10, le=3600)
    publishing_worker_batch_size: int = Field(default=10, ge=1, le=100)
    publishing_worker_stale_seconds: int = Field(default=90, ge=10, le=86400)
    publishing_notification_max_attempts: int = Field(default=3, ge=1, le=20)
    publishing_platform_capabilities: dict[str, dict[str, object]] = Field(default_factory=dict)
    publishing_x_api_base_url: str = "https://api.x.com/2"
    publishing_instagram_api_base_url: str = "https://graph.instagram.com"
    publishing_meta_graph_api_version: str = ""
    publishing_tiktok_api_base_url: str = "https://open.tiktokapis.com/v2"
    publishing_youtube_api_base_url: str = "https://www.googleapis.com"
    publishing_pinterest_api_base_url: str = "https://api.pinterest.com/v5"
    publishing_reddit_api_base_url: str = "https://oauth.reddit.com"
    x_oauth_access_token: str = ""
    x_client_id: str = ""
    x_client_secret: str = ""
    x_redirect_uri: str = ""
    x_oauth_authorization_url: str = "https://x.com/i/oauth2/authorize"
    x_oauth_token_url: str = "https://api.x.com/2/oauth2/token"  # noqa: S105
    x_oauth_revoke_url: str = "https://api.x.com/2/oauth2/revoke"
    meta_app_id: str = ""
    meta_app_secret: str = ""
    meta_redirect_uri: str = ""
    instagram_oauth_authorization_url: str = "https://www.instagram.com/oauth/authorize"
    instagram_oauth_token_url: str = "https://api.instagram.com/oauth/access_token"  # noqa: S105
    instagram_long_lived_token_url: str = "https://graph.instagram.com/access_token"  # noqa: S105
    instagram_refresh_token_url: str = "https://graph.instagram.com/refresh_access_token"  # noqa: S105
    tiktok_client_key: str = ""
    tiktok_client_secret: str = ""
    tiktok_redirect_uri: str = ""
    tiktok_oauth_authorization_url: str = "https://www.tiktok.com/v2/auth/authorize/"
    tiktok_oauth_token_url: str = "https://open.tiktokapis.com/v2/oauth/token/"  # noqa: S105
    tiktok_oauth_revoke_url: str = "https://open.tiktokapis.com/v2/oauth/revoke/"
    tiktok_oauth_use_pkce: bool = True
    tiktok_client_audited: bool = False
    publishing_oauth_timeout_seconds: float = Field(default=30.0, ge=5, le=120)
    pinterest_app_id: str = ""
    pinterest_app_secret: str = ""
    pinterest_redirect_uri: str = ""
    pinterest_oauth_authorization_url: str = "https://www.pinterest.com/oauth/"
    pinterest_oauth_token_url: str = "https://api.pinterest.com/v5/oauth/token"  # noqa: S105
    pinterest_oauth_continuous_refresh: bool = True

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
    def ai_council_provider_models(self) -> dict[str, str]:
        """Return configured text-model providers without exposing credentials."""
        providers: dict[str, str] = {}
        if self.openai_api_key and self.ai_council_openai_model.strip():
            providers["openai"] = self.ai_council_openai_model.strip()
        if self.claude_api_key and self.ai_council_anthropic_model.strip():
            providers["anthropic"] = self.ai_council_anthropic_model.strip()
        if self.effective_gemini_api_key and self.ai_council_gemini_model.strip():
            providers["gemini"] = self.ai_council_gemini_model.strip()
        return providers

    @property
    def effective_gemini_api_key(self) -> str:
        return self.gemini_api_key or self.google_api_key

    @property
    def market_intelligence_web_feeds(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.market_intelligence_web_feed_urls.split(",")
            if value.strip()
        )

    @property
    def effective_creative_asset_dir(self) -> Path:
        if self.creative_asset_dir.strip():
            return Path(self.creative_asset_dir).expanduser().resolve()
        return (PROJECT_ROOT / "data" / "creative_assets").resolve()

    @property
    def effective_publishing_performance_windows(self) -> tuple[str, ...]:
        return tuple(
            value.strip()
            for value in self.publishing_performance_windows.split(",")
            if value.strip()
        )

    @property
    def pinterest_oauth_configured(self) -> bool:
        return bool(
            self.pinterest_app_id.strip()
            and self.pinterest_app_secret.strip()
            and self.pinterest_redirect_uri.strip()
        )

    @property
    def x_oauth_configured(self) -> bool:
        return bool(
            self.x_client_id.strip()
            and self.x_client_secret.strip()
            and self.x_redirect_uri.strip()
        )

    @property
    def instagram_publishing_configured(self) -> bool:
        return bool(
            self.meta_app_id.strip()
            and self.meta_app_secret.strip()
            and self.meta_redirect_uri.strip()
            and self.publishing_meta_graph_api_version.strip()
        )

    @property
    def tiktok_oauth_configured(self) -> bool:
        return bool(
            self.tiktok_client_key.strip()
            and self.tiktok_client_secret.strip()
            and self.tiktok_redirect_uri.strip()
        )

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
