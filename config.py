from pydantic_settings import BaseSettings,SettingsConfigDict
from functools import lru_cache


class Config(BaseSettings):
    github_token: str | None = None
    voyage_api_key: str
    embedding_provider: str = "voyage"
    embedding_model_name: str = "voyage-code-2"
    embedding_base_url: str | None = None
    data_dir: str = "~/.repo-ai"
    model_config = SettingsConfigDict(
        env_file=".env",           # Read from .env if it exists locally
        env_file_encoding="utf-8",
        extra="ignore",            # Ignore other random environment rules
        case_sensitive=False       # Allows mapping 'github_token' to 'GITHUB_TOKEN'
    )


@lru_cache
def get_config() -> Config:
    return Config()


config = get_config()
