"""Application configuration via pydantic-settings."""

from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Ollama Cloud (LLM chat completions)
    ollama_cloud_base_url: str = "https://api.ollama.com/v1"
    ollama_cloud_api_key: str = ""
    ollama_cloud_model: str = "llama3.2"

    # Local Ollama (embeddings)
    ollama_embed_base_url: str = "http://localhost:11434"
    ollama_embed_model: str = "qwen3-embedding"
    ollama_embed_dimensions: int = 1536

    # Embeddings
    embedding_dimension: int = 1536

    # Storage paths
    lancedb_path: str = "/data/lancedb"
    documents_path: str = "/data/documents"

    # Google OAuth
    google_client_id: str = ""
    google_client_secret: str = ""

    # JWT
    jwt_private_key_path: str = "./secrets/jwt_private_key.pem"
    jwt_public_key_path: str = "./secrets/jwt_public_key.pem"
    jwt_expiry_minutes: int = 10
    jwt_refresh_expiry_days: int = 7

    # Frontend
    frontend_origin: str = "http://localhost:3000"

    # File ingestion
    allowed_folder_roots: list[str] = ["/data/documents"]
    max_file_size_mb: int = 100

    # Rate limiting
    rate_limit_per_user: int = 60
    rate_limit_window_seconds: int = 60

    # Logging
    rust_log: str = "info"

    @property
    def jwt_private_key(self) -> Path:
        return Path(self.jwt_private_key_path)

    @property
    def jwt_public_key(self) -> Path:
        return Path(self.jwt_public_key_path)

    @property
    def jwt_private_key_pem(self) -> str:
        if not self.jwt_private_key.exists():
            return ""
        return self.jwt_private_key.read_text()

    @property
    def jwt_public_key_pem(self) -> str:
        if not self.jwt_public_key.exists():
            return ""
        return self.jwt_public_key.read_text()


@lru_cache
def get_settings() -> Settings:
    return Settings()
