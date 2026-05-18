"""
AgriMesh V4.0 — Application Configuration
Centralized settings with env-var overrides.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All AgriMesh configuration, loaded from .env / environment."""

    # ─── Runtime / Security ──────────────────
    app_env: Literal["development", "test", "demo", "production"] = Field(
        default="development",
        alias="APP_ENV",
    )
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"], alias="ALLOWED_ORIGINS")
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "0.0.0.0", "testserver"], alias="ALLOWED_HOSTS")
    api_key: str = Field(default="", alias="AGRIMESH_API_KEY")
    require_api_key: bool = Field(default=True, alias="AGRIMESH_REQUIRE_API_KEY")
    max_request_bytes: int = Field(default=2_000_000, alias="MAX_REQUEST_BYTES")
    bot_demo_secret_current: str = Field(default="", alias="BOT_DEMO_SECRET_CURRENT")
    bot_demo_secret_previous: str = Field(default="", alias="BOT_DEMO_SECRET_PREVIOUS")
    demo_allowed_farmers: list[str] = Field(default_factory=list, alias="DEMO_ALLOWED_FARMERS")
    demo_allowed_workers: list[str] = Field(default_factory=list, alias="DEMO_ALLOWED_WORKERS")
    enable_demo_sessions: bool = Field(default=False, alias="ENABLE_DEMO_SESSIONS")
    demo_mode: bool = Field(default=False, alias="DEMO_MODE")
    enable_dev_farmer_header: bool = Field(default=False, alias="ENABLE_DEV_FARMER_HEADER")
    eval_public_token: str = Field(default="", alias="EVAL_PUBLIC_TOKEN")

    # ─── Ollama ───────────────────────────
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="gemma4:e4b", alias="OLLAMA_MODEL")
    ollama_fallback_model: str = Field(default="gemma4:e2b", alias="OLLAMA_FALLBACK_MODEL")
    ollama_num_ctx: int = Field(default=16384, alias="OLLAMA_NUM_CTX")
    ollama_num_batch: int = Field(default=512, alias="OLLAMA_NUM_BATCH")
    ollama_keep_alive: int = Field(default=-1, alias="OLLAMA_KEEP_ALIVE")
    ollama_max_concurrency: int = Field(default=2, alias="OLLAMA_MAX_CONCURRENCY")
    ollama_timeout_seconds: float = Field(default=90.0, alias="OLLAMA_TIMEOUT_SECONDS")
    ollama_fallback_cooldown_seconds: float = Field(
        default=60.0,
        alias="OLLAMA_FALLBACK_COOLDOWN_SECONDS",
    )
    use_grammar_decoding: bool = Field(default=True, alias="USE_GRAMMAR_DECODING")

    # Gemma 4 sampling (Google's official defaults — DO NOT CHANGE)
    temperature: float = 1.0
    top_p: float = 0.95
    top_k: int = 64

    # ─── Database ─────────────────────────
    database_url: str = Field(
        default="sqlite+aiosqlite:///./data/agrimesh.db",
        alias="DATABASE_URL",
    )

    # ─── Retrieval ────────────────────────
    bge_m3_model: str = Field(default="BAAI/bge-m3", alias="BGE_M3_MODEL")
    bge_reranker_model: str = Field(
        default="BAAI/bge-reranker-v2-m3", alias="BGE_RERANKER_MODEL"
    )
    retrieval_top_k: int = Field(default=10, alias="RETRIEVAL_TOP_K")
    reranker_top_k: int = Field(default=3, alias="RERANKER_TOP_K")
    embedding_max_concurrency: int = Field(default=2, alias="EMBEDDING_MAX_CONCURRENCY")

    # ─── Weather API ─────────────────────
    weather_api_key: str = Field(default="", alias="WEATHER_API_KEY")

    # ─── Telegram ─────────────────────────
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    dashboard_base_url: str = Field(default="http://localhost:8000", alias="AGRIMESH_DASHBOARD_BASE_URL")

    # ─── MCP Ports ────────────────────────
    mcp_weather_port: int = Field(default=9001, alias="MCP_WEATHER_PORT")
    mcp_mandi_port: int = Field(default=9002, alias="MCP_MANDI_PORT")
    mcp_scheme_port: int = Field(default=9003, alias="MCP_SCHEME_PORT")
    mcp_finance_port: int = Field(default=9004, alias="MCP_FINANCE_PORT")
    mcp_crop_kb_port: int = Field(default=9005, alias="MCP_CROP_KB_PORT")

    # ─── Sarvam AI (voice pipeline) ───────
    sarvam_api_key: str = Field(default="", alias="SARVAM_API_KEY")
    sarvam_base_url: str = Field(default="https://api.sarvam.ai", alias="SARVAM_BASE_URL")
    sarvam_stt_model: str = Field(default="saarika:v2.5", alias="SARVAM_STT_MODEL")
    sarvam_translate_model: str = Field(default="mayura:v1", alias="SARVAM_TRANSLATE_MODEL")
    sarvam_tts_model: str = Field(default="bulbul:v2", alias="SARVAM_TTS_MODEL")
    sarvam_tts_speaker: str = Field(default="meera", alias="SARVAM_TTS_SPEAKER")
    sarvam_timeout_seconds: float = Field(default=30.0, alias="SARVAM_TIMEOUT_SECONDS")
    enable_voice_pipeline: bool = Field(default=False, alias="ENABLE_VOICE_PIPELINE")
    voice_default_target_lang: str = Field(default="hi-IN", alias="VOICE_DEFAULT_TARGET_LANG")

    # ─── Feature Flags ────────────────────
    use_gemma_audio: bool = Field(default=False, alias="USE_GEMMA_AUDIO")
    enable_voice_stt: bool = Field(default=False, alias="ENABLE_VOICE_STT")
    enable_sms: bool = Field(default=False, alias="ENABLE_SMS")
    enable_bhojpuri: bool = Field(default=False, alias="ENABLE_BHOJPURI")

    # ─── Paths ────────────────────────────
    data_dir: Path = Field(default=Path("./data"), alias="DATA_DIR")
    seed_dir: Path = Field(default=Path("./data/seed"), alias="SEED_DIR")
    wiki_dir: Path = Field(default=Path("./wiki/articles"), alias="WIKI_DIR")
    eval_dir: Path = Field(default=Path("./evals"), alias="EVAL_DIR")

    # ─── Logging ──────────────────────────
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "allow",
        "populate_by_name": True,
    }

    @field_validator(
        "allowed_origins",
        "allowed_hosts",
        "demo_allowed_farmers",
        "demo_allowed_workers",
        mode="before",
    )
    @staticmethod
    def _parse_csv_list(value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @model_validator(mode="after")
    def _validate_environment_policy(self):
        placeholders = {
            "",
            "changeme",
            "change-me",
            "placeholder",
            "replace-me",
            "your-api-key",
            "your-secret",
            "secret",
            "test",
            "dev",
        }
        errors = self.startup_errors(placeholders=placeholders)
        if errors:
            raise ValueError("; ".join(errors))

        if (
            self.app_env == "demo"
            and self.enable_demo_sessions
            and len(self.bot_demo_secret_current) < 32
        ):
            raise ValueError(
                "BOT_DEMO_SECRET_CURRENT must be at least 32 chars when demo sessions are enabled"
            )
        return self

    def startup_errors(self, placeholders: set[str] | None = None) -> list[str]:
        """Return actionable configuration errors before the API starts serving traffic."""
        placeholders = placeholders or {
            "",
            "changeme",
            "change-me",
            "placeholder",
            "replace-me",
            "your-api-key",
            "your-secret",
            "secret",
            "test",
            "dev",
        }
        errors: list[str] = []
        api_key = self.api_key.strip()
        telegram_token = self.telegram_bot_token.strip()
        parsed_db = urlparse(self.database_url)
        parsed_ollama = urlparse(self.ollama_host)

        if not parsed_db.scheme:
            errors.append("DATABASE_URL must include a SQLAlchemy scheme")
        if self.max_request_bytes < 1024:
            errors.append("MAX_REQUEST_BYTES must be at least 1024")
        if self.ollama_timeout_seconds <= 0:
            errors.append("OLLAMA_TIMEOUT_SECONDS must be greater than 0")
        if self.ollama_max_concurrency < 1:
            errors.append("OLLAMA_MAX_CONCURRENCY must be at least 1")
        if parsed_ollama.scheme not in {"http", "https"} or not parsed_ollama.netloc:
            errors.append("OLLAMA_HOST must be an http(s) URL, for example http://localhost:11434")
        if self.retrieval_top_k < 1:
            errors.append("RETRIEVAL_TOP_K must be at least 1")
        if self.reranker_top_k < 1:
            errors.append("RERANKER_TOP_K must be at least 1")

        if self.app_env == "production":
            if len(api_key) < 32 or api_key.lower() in placeholders:
                errors.append("AGRIMESH_API_KEY must be a non-placeholder value of at least 32 chars")
            if self.allowed_origins == ["*"]:
                errors.append("ALLOWED_ORIGINS cannot be '*' in production")
            if self.database_url.startswith("sqlite"):
                errors.append("DATABASE_URL cannot use SQLite in production")
            if not telegram_token or telegram_token.lower() in placeholders:
                errors.append("TELEGRAM_BOT_TOKEN must be configured in production")
            if self.enable_demo_sessions:
                errors.append("ENABLE_DEMO_SESSIONS must be false in production")
            if self.enable_dev_farmer_header:
                errors.append("ENABLE_DEV_FARMER_HEADER must be false in production")
            if self.demo_allowed_farmers:
                errors.append("DEMO_ALLOWED_FARMERS must be empty in production")
            if self.demo_allowed_workers:
                errors.append("DEMO_ALLOWED_WORKERS must be empty in production")
            if self.bot_demo_secret_current:
                errors.append("BOT_DEMO_SECRET_CURRENT must be empty in production")
            if self.bot_demo_secret_previous:
                errors.append("BOT_DEMO_SECRET_PREVIOUS must be empty in production")

        return errors

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    @property
    def api_key_required(self) -> bool:
        return self.require_api_key or self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
