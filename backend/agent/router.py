from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from qdrant_client import QdrantClient
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext, VectorStoreIndex

from auth.dependencies import get_current_user
from agent.graph import build_agent_graph
from agent.state import AgentState
from config import get_settings


router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    query: str


def _collection_for_department(department: Optional[str]) -> str:
    settings = get_settings()
    dep = (department or "").strip().lower()
    if dep in {"clinical", "clinician"}:
        return settings.clinical_collection_name
    if dep in {"management", "manager"}:
        return settings.manager_collection_name
    return settings.default_collection_name


@router.post("/chat")
async def chat(payload: ChatRequest, user=Depends(get_current_user)):
    settings = get_settings()
    client = QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        check_compatibility=False,
    )
    collection = _collection_for_department(user.department)
    vector_store = QdrantVectorStore(client=client, collection_name=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    graph = build_agent_graph()
    state: AgentState = {
        "session_id": payload.session_id,
        "query": payload.query,
        "user_id": str(user.id),
        "user_role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "user_department": user.department,
        "index": index,
        "storage_context": storage_context,
    }
    result = await graph.ainvoke(state)
    return {
        "answer": result.get("answer"),
        "citations": result.get("citations"),
        "confidence": result.get("confidence"),
        "stakes_level": result.get("stakes_level"),
        "gap_ticket_id": result.get("gap_ticket_id"),
    }


@router.get("/chat/stream")
async def chat_stream(_user=Depends(get_current_user)):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Streaming chat not implemented yet")


@router.get("/sessions")
async def list_sessions(_user=Depends(get_current_user)):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Session listing not implemented yet")


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, _user=Depends(get_current_user)):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Session delete not implemented yet")
