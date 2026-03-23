"""Application configuration via pydantic-settings."""

from pathlib import Path
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolve .env from repo root (two levels above this file: app/ → python-api/ → repo root)
_REPO_ROOT = Path(__file__).parent.parent.parent
_ENV_FILE = _REPO_ROOT / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Ollama Cloud (LLM chat completions)
    ollama_cloud_base_url: str = "https://api.ollama.com/v1"
    ollama_cloud_api_key: str = ""
    ollama_cloud_model: str = "llama3.2"

    # HuggingFace local embeddings (sentence-transformers)
    hf_embed_model: str = "Qwen/Qwen3-Embedding-0.6B"
    hf_token: str = ""

    # Embeddings
    embedding_dimension: int = 1024

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
    frontend_origin: str = "http://localhost:5333"

    # Cookie security — set True in production (HTTPS); False for local HTTP dev
    cookie_secure: bool = False

    # File ingestion — comma-separated string to avoid pydantic-settings JSON parse issues
    allowed_folder_roots: str = "/data/documents"

    @property
    def allowed_folder_roots_list(self) -> list[str]:
        return [p.strip() for p in self.allowed_folder_roots.split(",") if p.strip()]
    max_file_size_mb: int = 100

    # Rate limiting
    rate_limit_per_user: int = 60
    rate_limit_window_seconds: int = 60

    # Multimodal (vision captions for PDF images)
    vision_model: str = "gpt-4o-mini"          # OpenAI vision model for image captions
    vision_enabled: bool = True                 # Set False to skip image extraction
    vision_max_pages: int = 50                  # Max pages to extract images from per PDF
    vision_image_dpi: int = 72                  # Resolution for page renders (lower = faster)

    # Drive webhook
    drive_webhook_url: str = ""                 # Public HTTPS URL for Drive push notifications

    # Fine-tuning
    openai_api_key: str = ""                    # Required for fine-tuning API

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
