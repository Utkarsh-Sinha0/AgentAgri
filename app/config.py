"""
AgriMesh V4.0 — Application Configuration
Centralized settings with env-var overrides.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    """All AgriMesh configuration, loaded from .env / environment."""

    # ─── Runtime / Security ──────────────────
    app_env: str = Field(default="development", alias="APP_ENV")
    allowed_origins: list[str] = Field(default_factory=lambda: ["http://localhost:8000"], alias="ALLOWED_ORIGINS")
    allowed_hosts: list[str] = Field(default_factory=lambda: ["localhost", "127.0.0.1", "0.0.0.0", "testserver"], alias="ALLOWED_HOSTS")
    api_key: str = Field(default="", alias="AGRIMESH_API_KEY")
    require_api_key: bool = Field(default=False, alias="AGRIMESH_REQUIRE_API_KEY")
    max_request_bytes: int = Field(default=2_000_000, alias="MAX_REQUEST_BYTES")

    # ─── Ollama ───────────────────────────
    ollama_host: str = Field(default="http://localhost:11434", alias="OLLAMA_HOST")
    ollama_model: str = Field(default="gemma4:e4b", alias="OLLAMA_MODEL")
    ollama_fallback_model: str = Field(default="gemma4:e2b", alias="OLLAMA_FALLBACK_MODEL")
    ollama_num_ctx: int = Field(default=16384, alias="OLLAMA_NUM_CTX")
    ollama_num_batch: int = Field(default=512, alias="OLLAMA_NUM_BATCH")
    ollama_keep_alive: int = Field(default=-1, alias="OLLAMA_KEEP_ALIVE")
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

    # ─── Telegram ─────────────────────────
    telegram_bot_token: str = Field(default="", alias="TELEGRAM_BOT_TOKEN")
    dashboard_base_url: str = Field(default="http://localhost:8000", alias="AGRIMESH_DASHBOARD_BASE_URL")

    # ─── MCP Ports ────────────────────────
    mcp_weather_port: int = Field(default=9001, alias="MCP_WEATHER_PORT")
    mcp_mandi_port: int = Field(default=9002, alias="MCP_MANDI_PORT")
    mcp_scheme_port: int = Field(default=9003, alias="MCP_SCHEME_PORT")
    mcp_finance_port: int = Field(default=9004, alias="MCP_FINANCE_PORT")

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

    model_config = {"env_file": ".env", "env_file_encoding": "utf-8", "extra": "allow"}

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _parse_csv_list(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @property
    def is_production(self) -> bool:
        return self.app_env.lower() in {"prod", "production"}

    @property
    def api_key_required(self) -> bool:
        return self.require_api_key or self.is_production


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
