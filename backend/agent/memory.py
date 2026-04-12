from datetime import datetime
import logging

from pymongo.errors import PyMongoError
from db.mongo import get_mongo_db

logger = logging.getLogger(__name__)


async def load_session_history(session_id: str, window: int = 10) -> list:
    try:
        db = get_mongo_db()
        doc = await db.sessions.find_one({"session_id": session_id})
        if not doc:
            return []
        return doc.get("messages", [])[-window:]
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in load_session_history(session_id=%s): %s", session_id, exc)
        return []


async def save_to_session(session_id: str, user_id: str, turn: dict):
    try:
        db = get_mongo_db()
        turn["timestamp"] = turn.get("timestamp") or datetime.utcnow()
        await db.sessions.update_one(
            {"session_id": session_id},
            {
                "$push": {"messages": turn},
                "$set": {"user_id": user_id, "last_active": datetime.utcnow()},
                "$setOnInsert": {"session_id": session_id, "created_at": datetime.utcnow()},
            },
            upsert=True,
        )
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in save_to_session(session_id=%s): %s", session_id, exc)


async def list_sessions(user_id: str) -> list:
    try:
        db = get_mongo_db()
        cursor = db.sessions.find({"user_id": user_id}).sort("last_active", -1)
        sessions = []
        async for doc in cursor:
            sessions.append(
                {
                    "session_id": doc.get("session_id"),
                    "user_id": doc.get("user_id"),
                    "created_at": doc.get("created_at"),
                    "last_active": doc.get("last_active"),
                    "message_count": len(doc.get("messages", [])),
                }
            )
        return sessions
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in list_sessions(user_id=%s): %s", user_id, exc)
        return []


async def delete_session(session_id: str, user_id: str) -> bool:
    try:
        db = get_mongo_db()
        result = await db.sessions.delete_one({"session_id": session_id, "user_id": user_id})
        return result.deleted_count > 0
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in delete_session(session_id=%s): %s", session_id, exc)
        return False


async def get_session(session_id: str, user_id: str) -> dict | None:
    try:
        db = get_mongo_db()
        doc = await db.sessions.find_one({"session_id": session_id, "user_id": user_id})
        if not doc:
            return None
        return {
            "session_id": doc.get("session_id"),
            "user_id": doc.get("user_id"),
            "created_at": doc.get("created_at"),
            "last_active": doc.get("last_active"),
            "messages": doc.get("messages", []),
        }
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in get_session(session_id=%s): %s", session_id, exc)
        return None
