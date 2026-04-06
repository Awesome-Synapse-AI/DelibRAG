from functools import lru_cache
from pathlib import Path

from pydantic import AnyUrl

try:
    from pydantic_settings import BaseSettings, SettingsConfigDict
except ImportError:  # pragma: no cover
    from pydantic import BaseSettings  # type: ignore
    SettingsConfigDict = None  # type: ignore


class Settings(BaseSettings):
    app_name: str = "DelibRAG Backend"
    database_url: AnyUrl
    jwt_secret: str
    jwt_algorithm: str = "HS256"
    access_token_exp_minutes: int = 30
    refresh_token_exp_days: int = 7
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    clinical_collection_name: str = "clinical-info"
    manager_collection_name: str = "manager-info"
    default_collection_name: str = "delibrag"
    scope_manifest_path: str = "scope_manifest.json"
    lda_model_path: str = "lda_model.pkl"
    lda_vectorizer_path: str = "lda_vectorizer.pkl"
    gap_retrieval_score_threshold: float = 0.2
    gap_confidence_threshold: float = 0.45
    mongo_uri: str = "mongodb://mongo:27017"
    mongo_db_name: str = "delibrag"
    openai_api_key: str = ""

    _backend_dir = Path(__file__).resolve().parent
    _env_paths = (
        str(_backend_dir / ".env"),
        str(_backend_dir.parent / ".env"),
    )

    if SettingsConfigDict is not None:
        model_config = SettingsConfigDict(
            env_file=_env_paths,
            env_file_encoding="utf-8",
        )
    else:  # pragma: no cover
        class Config:
            env_file = _env_paths[1]
            env_file_encoding = "utf-8"


@lru_cache()
def get_settings() -> Settings:
    return Settings()
