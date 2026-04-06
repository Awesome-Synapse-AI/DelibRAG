from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from db.mongo import get_mongo_db


async def write_audit_entry(entry: Dict[str, Any]) -> str:
    db = get_mongo_db()
    doc = dict(entry)
    doc.setdefault("timestamp", datetime.now(timezone.utc))
    result = await db.audit_trails.insert_one(doc)
    return str(result.inserted_id)


async def get_audit_for_session(session_id: str) -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db.audit_trails.find({"session_id": session_id}).sort("timestamp", -1)
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        rows.append(_serialize_doc(doc))
    return rows


async def get_audit_for_query(query_id: str) -> Optional[Dict[str, Any]]:
    db = get_mongo_db()
    doc = await db.audit_trails.find_one({"query_id": query_id})
    if not doc:
        return None
    return _serialize_doc(doc)


async def export_all_audits() -> List[Dict[str, Any]]:
    db = get_mongo_db()
    cursor = db.audit_trails.find({}).sort("timestamp", -1)
    rows: List[Dict[str, Any]] = []
    async for doc in cursor:
        rows.append(_serialize_doc(doc))
    return rows


def _serialize_doc(doc: Dict[str, Any]) -> Dict[str, Any]:
    out = dict(doc)
    if "_id" in out:
        out["_id"] = str(out["_id"])
    timestamp = out.get("timestamp")
    if hasattr(timestamp, "isoformat"):
        out["timestamp"] = timestamp.isoformat()
    return out
