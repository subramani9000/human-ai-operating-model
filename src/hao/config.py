import os
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo root for policy/ and data/. Override with HAO_HOME when installed outside the repo (e.g. Docker).
REPO_ROOT = Path(os.environ.get("HAO_HOME", Path(__file__).resolve().parents[2]))


class Settings(BaseSettings):
    """Runtime configuration. Every value can be overridden by an env var or `.env`."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = f"sqlite:///{REPO_ROOT / 'hao.db'}"
    policy_dir: Path = REPO_ROOT / "policy"

    # Mock ticketing system (simulated client)
    mock_api_key: str = "kaveri-dev-key"
    webhook_secret: str = "kaveri-webhook-secret"
    mock_failure_rate: float = 0.0  # 0.0–1.0: share of API calls that return 503
    mock_latency_ms: int = 0  # artificial latency per request
    mock_webhook_duplicate_rate: float = 0.0  # share of webhooks delivered twice (at-least-once)
    webhook_max_attempts: int = 3


@lru_cache
def get_settings() -> Settings:
    return Settings()
