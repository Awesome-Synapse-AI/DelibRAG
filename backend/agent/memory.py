from datetime import datetime
import logging

from pymongo.errors import PyMongoError
from llama_index.llms.openai import OpenAI

from db.mongo import get_mongo_db

logger = logging.getLogger(__name__)


async def _generate_session_title(user_query: str, assistant_answer: str) -> str:
    """Generate a concise session title using LLM based on the first exchange."""
    try:
        llm = OpenAI(model="gpt-4o-mini", temperature=0.3)
        prompt = f"""Generate a concise title (maximum 10 words) for a conversation based on this exchange:

User: {user_query}

Assistant: {assistant_answer}

Title (10 words max, no quotes):"""
        
        response = await llm.acomplete(prompt)
        title = str(response).strip().strip('"').strip("'")
        
        # Ensure it's not too long
        words = title.split()
        if len(words) > 10:
            title = " ".join(words[:10])
        
        return title if title else "New conversation"
    except Exception as exc:
        logger.warning("Failed to generate session title: %s", exc)
        # Fallback to simple truncation
        return user_query[:50] + "..." if len(user_query) > 50 else user_query


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
        
        # Check if session exists and has a title
        existing = await db.sessions.find_one({"session_id": session_id})
        
        # Basic update operations
        update_ops = {
            "$push": {"messages": turn},
            "$set": {"user_id": user_id, "last_active": datetime.utcnow()},
            "$setOnInsert": {"session_id": session_id, "created_at": datetime.utcnow()},
        }
        
        await db.sessions.update_one(
            {"session_id": session_id},
            update_ops,
            upsert=True,
        )
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in save_to_session(session_id=%s): %s", session_id, exc)


async def generate_and_save_title(session_id: str, user_query: str, assistant_answer: str):
    """Generate and save a title for a session based on the first exchange."""
    try:
        db = get_mongo_db()
        
        # Check if session already has a title
        existing = await db.sessions.find_one({"session_id": session_id})
        if existing and existing.get("title"):
            return  # Already has a title
        
        # Generate title using LLM
        title = await _generate_session_title(user_query, assistant_answer)
        
        # Save the title
        await db.sessions.update_one(
            {"session_id": session_id},
            {"$set": {"title": title}},
        )
        logger.info("Generated title for session %s: %s", session_id, title)
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in generate_and_save_title(session_id=%s): %s", session_id, exc)
    except Exception as exc:
        logger.warning("Failed to generate and save title for session %s: %s", session_id, exc)


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
                    "title": doc.get("title", "New conversation"),
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
            "title": doc.get("title", "New conversation"),
        }
    except PyMongoError as exc:
        logger.warning("Mongo unavailable in get_session(session_id=%s): %s", session_id, exc)
        return None
