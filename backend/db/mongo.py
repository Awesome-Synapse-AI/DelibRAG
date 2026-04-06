from motor.motor_asyncio import AsyncIOMotorClient

from config import get_settings


_client: AsyncIOMotorClient | None = None


def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncIOMotorClient(
            settings.mongo_uri,
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
            socketTimeoutMS=5000,
        )
    return _client


def get_mongo_db():
    settings = get_settings()
    return get_mongo_client()[settings.mongo_db_name]
