from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://trap:trap@db:5432/hallucination_trap"
    openai_api_key: str | None = None
    azure_openai_endpoint: str | None = None
    azure_openai_key: str | None = None
    embedding_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
        "@faf4aa4225822f3bc6376869cb1164e8e3feedd0"
    )
    reranker_model: str = (
        "onnx-community/gte-multilingual-reranker-base"
        "@ee64367e35a2db0da46bb6497e13a18f8bd585cb"
    )
    cache_mode: str = "cached"
    openai_model: str = "gpt-4.1-mini"
    retrieval_confidence_threshold: float = Field(default=0.44, ge=0, le=1)
    retrieve_timeout_seconds: float = Field(default=4, gt=0)
    rank_timeout_seconds: float = Field(default=12, gt=0)
    generate_timeout_seconds: float = Field(default=25, gt=0)
    parse_timeout_seconds: float = Field(default=2, gt=0)
    verify_timeout_seconds: float = Field(default=2, gt=0)
    cors_origins: str = "http://localhost:5173,http://localhost:8080"
    model_cache_dir: Path = Path(".cache/models")

    @field_validator("cache_mode")
    @classmethod
    def validate_cache_mode(cls, value: str) -> str:
        normalized = value.strip().lower()
        if normalized not in {"cached", "live"}:
            raise ValueError("CACHE_MODE must be 'cached' or 'live'")
        return normalized

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def has_live_provider(self) -> bool:
        return bool(
            self.openai_api_key
            or (self.azure_openai_endpoint and self.azure_openai_key)
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
