from functools import lru_cache
from typing import Optional

from pydantic import BaseSettings, AnyUrl


class Settings(BaseSettings):
    app_name: str = "DelibRAG Backend"
    database_url: AnyUrl
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 7

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
