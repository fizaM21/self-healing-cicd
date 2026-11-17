from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "sqlite:///./selfheal.db"
    # LLM: groq | gemini | heuristic (heuristic needs no key)
    llm_provider: str = "heuristic"
    groq_api_key: str = ""
    groq_model: str = "llama-3.3-70b-versatile"
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.0-flash"

    github_token: str = ""
    github_webhook_secret: str = ""
    # Key the dashboard/admin calls must send in X-API-Key
    api_key: str = ""
    # Where to clone from. {repo} = owner/name. Tests point this at a local path.
    git_remote_template: str = "https://github.com/{repo}.git"

    sandbox_mode: str = "docker"  # docker | local
    sandbox_image_python: str = "python:3.12-slim"
    sandbox_image_node: str = "node:22-slim"
    sandbox_timeout_s: int = 600
    sandbox_workdir: str = ""  # shared dir for clones (must exist at the same path on the Docker host)
    default_test_command: str = ""  # empty = auto-detect

    cors_origins: str = "http://localhost:3000"
    max_log_chars: int = 15000

    @field_validator("database_url")
    @classmethod
    def _fix_scheme(cls, v: str) -> str:
        # Render/Heroku hand out postgres:// but SQLAlchemy 2.x only accepts postgresql://
        return "postgresql://" + v[len("postgres://"):] if v.startswith("postgres://") else v


@lru_cache
def get_settings() -> Settings:
    return Settings()
