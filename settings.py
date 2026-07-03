import os
from pydantic_settings import BaseSettings
from pydantic import Field
from typing import List, Optional


class Settings(BaseSettings):
    # FastAPI
    PROJECT_NAME: str = "StarSage"
    API_V1_PREFIX: str = "/api"
    ALLOWED_HOSTS: List[str] = ["*"]
    DEBUG: bool = False

    # Database
    DATABASE_URL: str = Field(default="sqlite:///./starsage.sqlite3", env="DATABASE_URL")

    # LLM providers
    OPENAI_API_KEY: str = Field(default="", env="OPENAI_API_KEY")
    ANTHROPIC_API_KEY: Optional[str] = Field(default=None, env="ANTHROPIC_API_KEY")
    GEMINI_API_KEY: Optional[str] = Field(default=None, env="GEMINI_API_KEY")

    # Model selection
    DEFAULT_FAST_MODEL: str = Field(default="gpt-4o-mini", env="DEFAULT_FAST_MODEL")
    DEFAULT_REASONING_MODEL: str = Field(default="gpt-4o", env="DEFAULT_REASONING_MODEL")

    # Pipeline flags
    ENABLE_CRITIC_PASS: bool = Field(default=False, env="ENABLE_CRITIC_PASS")
    ENABLE_KB_RETRIEVAL: bool = Field(default=True, env="ENABLE_KB_RETRIEVAL")

    # Google Maps (for geocoding/timezone)
    GOOGLE_MAPS_API_KEY: Optional[str] = Field(default=None, env="GOOGLE_MAPS_API_KEY")

    # Legacy / optional cloud (kept so existing .env files don't break)
    AZURE_STORAGE_ACCOUNT: Optional[str] = Field(default=None, env="AZURE_STORAGE_ACCOUNT")
    AZURE_STORAGE_KEY: Optional[str] = Field(default=None, env="AZURE_STORAGE_KEY")
    AZURE_BLOB_CONTAINER: str = Field(default="astro-data", env="AZURE_BLOB_CONTAINER")
    FIREWORKS_API_KEY: Optional[str] = Field(default=None, env="FIREWORKS_API_KEY")

    class Config:
        case_sensitive = True


settings = Settings()
