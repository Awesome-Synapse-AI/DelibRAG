from datetime import datetime
from typing import List

from db.mongo import get_mongo_db


async def load_session_history(session_id: str, window: int = 10) -> list:
    db = get_mongo_db()
    doc = await db.sessions.find_one({"session_id": session_id})
    if not doc:
        return []
    return doc.get("messages", [])[-window:]


async def save_to_session(session_id: str, user_id: str, turn: dict):
    db = get_mongo_db()
    turn["timestamp"] = turn.get("timestamp") or datetime.utcnow()
    await db.sessions.update_one(
        {"session_id": session_id},
        {"$push": {"messages": turn}, "$set": {"user_id": user_id, "last_active": datetime.utcnow()}},
        upsert=True,
    )


async def list_sessions(user_id: str) -> list:
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


async def delete_session(session_id: str, user_id: str) -> bool:
    db = get_mongo_db()
    result = await db.sessions.delete_one({"session_id": session_id, "user_id": user_id})
    return result.deleted_count > 0
