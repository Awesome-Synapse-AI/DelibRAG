from typing import Optional

import json
import os
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client import AsyncQdrantClient, QdrantClient
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext, VectorStoreIndex

from auth.dependencies import get_current_user
from agent.graph import build_agent_graph
from agent.memory import (
    delete_session as delete_session_store,
    get_session as get_session_store,
    list_sessions as list_sessions_store,
)
from agent.nodes import (
    answer_stream,
    audit_log_node,
    build_prompt,
    confidence_check_node,
    extract_citation_details,
    extract_citations,
    gap_detect_node,
    gap_ticket_create_node,
    high_stakes_retrieve_node,
    load_history_node,
    low_stakes_retrieve_node,
    memory_save_node,
    out_of_scope_response_node,
    role_mismatch_answer_text,
    scope_check_node,
    stakes_classify_node,
)
from agent.state import AgentState
from agent.stakes_classifier import StakesClassifier
from config import get_settings
from db.postgres import get_db
from indexing.pipeline import get_collection_persist_dir


router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    query: str


class ChatDevRequest(BaseModel):
    role: str
    query: str


def _parse_semver(version: str) -> tuple[int, int, int]:
    cleaned = version.split("-")[0]
    parts = cleaned.split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _ensure_qdrant_ready(client: QdrantClient, collection: str):
    try:
        if not client.collection_exists(collection):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Qdrant collection '{collection}' not found. Run indexing first.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"Cannot reach Qdrant or validate collection '{collection}': {exc}",
        )

    try:
        info = client.info()
        version = getattr(info, "version", None)
        if version is None and isinstance(info, dict):
            version = info.get("version")
        if version and _parse_semver(str(version)) < (1, 10, 0):
            raise HTTPException(
                status.HTTP_500_INTERNAL_SERVER_ERROR,
                "Qdrant server version is too old for this backend. "
                "Use Qdrant >= 1.10 (recommended 1.17.x) or downgrade qdrant-client/llama-index packages.",
            )
    except HTTPException:
        raise
    except Exception:
        # If version probe fails, collection check above still guarantees basic connectivity.
        pass


def _collection_for_department(department: Optional[str]) -> str:
    settings = get_settings()
    dep = (department or "").strip().lower()
    if dep in {"clinical", "clinician"}:
        return settings.clinical_collection_name
    if dep in {"management", "manager"}:
        return settings.manager_collection_name
    return settings.default_collection_name


def _department_for_role(role: str) -> str:
    normalized = (role or "").strip().lower()
    if normalized in {"clinician", "clinical"}:
        return "clinical"
    if normalized in {"manager", "management"}:
        return "management"
    return "general"


def _effective_department(user) -> str:
    raw_department = (getattr(user, "department", None) or "").strip().lower()
    # Only use the department if it's a valid one
    if raw_department in {"clinical", "clinician", "management", "manager"}:
        # Normalize to standard names
        if raw_department in {"clinician"}:
            return "clinical"
        if raw_department in {"manager"}:
            return "management"
        return raw_department
    # Fall back to role-based department
    role_value = getattr(user, "role", "")
    role_text = role_value.value if hasattr(role_value, "value") else str(role_value)
    return _department_for_role(role_text)


def _build_index_for_department(department: str):
    settings = get_settings()
    client = QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        check_compatibility=False,
    )
    aclient = AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        check_compatibility=False,
    )
    collection = _collection_for_department(department)
    _ensure_qdrant_ready(client, collection)
    persist_dir = get_collection_persist_dir(collection)
    if not persist_dir.exists():
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Local index storage missing for collection '{collection}'. Run scripts/run_llamaindex.py first.",
        )
    vector_store = QdrantVectorStore(client=client, aclient=aclient, collection_name=collection)
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        persist_dir=str(persist_dir),
    )
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)
    return collection, index, storage_context


@router.post("/chat")
async def chat(payload: ChatRequest, user=Depends(get_current_user), db: AsyncSession = Depends(get_db)):
    settings = get_settings()
    if not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OPENAI_API_KEY is not set in backend container environment",
        )
    department = _effective_department(user)
    _collection, index, storage_context = _build_index_for_department(department)

    graph = build_agent_graph()
    state: AgentState = {
        "session_id": payload.session_id,
        "query": payload.query,
        "user_id": str(user.id),
        "user_role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "user_department": department,
        "index": index,
        "storage_context": storage_context,
        "db": db,
    }
    result = await graph.ainvoke(state)
    return {
        "answer": result.get("answer"),
        "citations": result.get("citations"),
        "citation_details": result.get("citation_details"),
        "confidence": result.get("confidence"),
        "stakes_level": result.get("stakes_level"),
        "gap_ticket_id": result.get("gap_ticket_id"),
        "requires_human_review": result.get("requires_human_review"),
        "query_id": result.get("query_id"),
    }


@router.post("/chat/dev/stakes")
async def chat_dev_stakes(payload: ChatDevRequest):
    settings = get_settings()
    if not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OPENAI_API_KEY is not set in backend container environment",
        )
    role = (payload.role or "").strip().lower()
    department = _department_for_role(role)
    _collection, index, storage_context = _build_index_for_department(department)

    stakes_classifier = StakesClassifier()
    stakes_classification = await stakes_classifier.classify(query=payload.query, user_role=role)
    stakes_level = stakes_classification["stakes_level"]

    state: AgentState = {
        "session_id": "dev-stakes-check",
        "query": payload.query,
        "user_id": "dev-chat-user",
        "user_role": role,
        "user_department": department,
        "stakes_level": stakes_level,
        "stakes_classification": stakes_classification,
        "index": index,
        "storage_context": storage_context,
    }

    if stakes_level == "high":
        result = await high_stakes_retrieve_node(state)
        retrieval_path = "high_stakes_retrieve"
    else:
        result = await low_stakes_retrieve_node(state)
        retrieval_path = "low_stakes_retrieve"

    nodes = result.get("retrieved_nodes") or []
    top_sources = []
    for n in nodes[:5]:
        meta = {}
        if hasattr(n, "metadata"):
            meta = n.metadata or {}
        elif hasattr(n, "node") and hasattr(n.node, "metadata"):
            meta = n.node.metadata or {}
        src = meta.get("doc_id") or meta.get("source_id") or meta.get("source")
        if src:
            top_sources.append(str(src))

    return {
        "stakes_level": stakes_level,
        "stakes_classification": stakes_classification,
        "retrieval_path": retrieval_path,
        "retrieved_count": len(nodes),
        "raw_vector_max_score": result.get("raw_vector_max_score"),
        "query_entities": result.get("query_entities", []),
        "top_sources": top_sources,
        "role": role,
        "department": department,
    }


@router.get("/chat/stream")
async def chat_stream(
    session_id: str = Query(...),
    query: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    settings = get_settings()
    if not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OPENAI_API_KEY is not set in backend container environment",
        )
    department = _effective_department(user)
    _collection, index, storage_context = _build_index_for_department(department)

    state: AgentState = {
        "session_id": session_id,
        "query": query,
        "user_id": str(user.id),
        "user_role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "user_department": department,
        "index": index,
        "storage_context": storage_context,
    }

    async def event_stream():
        working_state: AgentState = dict(state)
        working_state["db"] = db

        working_state = await load_history_node(working_state)
        working_state = await scope_check_node(working_state)
        scope_in = bool((working_state.get("scope_result") or {}).get("in_scope"))

        if not scope_in:
            working_state = await out_of_scope_response_node(working_state)
        else:
            working_state = await stakes_classify_node(working_state)
            stakes = working_state.get("stakes_level", "high")
            if stakes == "low":
                working_state = await low_stakes_retrieve_node(working_state)
            else:
                working_state = await high_stakes_retrieve_node(working_state)
            working_state = await gap_detect_node(working_state)

            streamed_text = ""
            if working_state.get("role_topic_mismatch"):
                streamed_text = role_mismatch_answer_text(working_state)
                working_state["answer"] = streamed_text
                working_state["citations"] = []
                working_state["citation_details"] = []
                yield f"data: {json.dumps({'type':'chunk','content': streamed_text})}\n\n"
            else:
                prompt = build_prompt(working_state)
                async for piece in answer_stream(prompt):
                    if not piece:
                        continue
                    # Handle both cumulative and token-delta stream styles.
                    delta = piece
                    if piece.startswith(streamed_text):
                        delta = piece[len(streamed_text) :]
                        streamed_text = piece
                    else:
                        streamed_text += piece
                    if delta:
                        yield f"data: {json.dumps({'type':'chunk','content':delta})}\n\n"

                working_state["answer"] = streamed_text
                nodes = working_state.get("retrieved_nodes") or []
                working_state["citations"] = extract_citations(nodes)
                working_state["citation_details"] = extract_citation_details(nodes)
            working_state = await confidence_check_node(working_state)
            working_state = await gap_detect_node(working_state)
            working_state = await gap_ticket_create_node(working_state)

        working_state = await audit_log_node(working_state)
        working_state = await memory_save_node(working_state)

        answer = working_state.get("answer") or ""
        yield (
            "data: "
            + json.dumps(
                {
                    "type": "final",
                    "answer": answer,
                    "citations": working_state.get("citations"),
                    "citation_details": working_state.get("citation_details"),
                    "confidence": working_state.get("confidence"),
                    "stakes_level": working_state.get("stakes_level"),
                    "gap_ticket_id": working_state.get("gap_ticket_id"),
                    "requires_human_review": working_state.get("requires_human_review"),
                    "query_id": working_state.get("query_id"),
                }
            )
            + "\n\n"
        )

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/sessions")
async def list_sessions(user=Depends(get_current_user)):
    return await list_sessions_store(str(user.id))


@router.get("/sessions/{session_id}")
async def get_session(session_id: str, user=Depends(get_current_user)):
    session = await get_session_store(session_id, str(user.id))
    if not session:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return session


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(get_current_user)):
    deleted = await delete_session_store(session_id, str(user.id))
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return {"deleted": True, "session_id": session_id}
