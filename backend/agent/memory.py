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
