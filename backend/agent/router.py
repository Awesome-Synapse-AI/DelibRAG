from typing import Optional

import asyncio
import json
import os
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from qdrant_client import AsyncQdrantClient, QdrantClient
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext, VectorStoreIndex

from auth.dependencies import get_current_user
from agent.graph import build_agent_graph
from agent.memory import delete_session as delete_session_store, list_sessions as list_sessions_store
from agent.nodes import (
    answer_stream,
    build_prompt,
    drop_deprecated_nodes,
    extract_citations,
    get_retriever_for_user,
    rerank_by_trust_score,
)
from agent.state import AgentState
from agent.stakes_classifier import classify_stakes
from agent.confidence_gate import confidence_gate
from agent.memory import load_session_history, save_to_session
from retrieval.context_builder import build_context_string
from retrieval.entity_filter import filter_nodes_by_query_entities
from retrieval.scope_classifier import ScopeClassifier, evaluate_scope_result
from config import get_settings
from indexing.pipeline import get_collection_persist_dir


router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
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


@router.post("/chat")
async def chat(payload: ChatRequest, user=Depends(get_current_user)):
    settings = get_settings()
    if not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OPENAI_API_KEY is not set in backend container environment",
        )
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
    collection = _collection_for_department(user.department)
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
async def chat_stream(payload: ChatRequest, user=Depends(get_current_user)):
    settings = get_settings()
    if not (settings.openai_api_key or os.getenv("OPENAI_API_KEY")):
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "OPENAI_API_KEY is not set in backend container environment",
        )
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
    collection = _collection_for_department(user.department)
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

    state: AgentState = {
        "session_id": payload.session_id,
        "query": payload.query,
        "user_id": str(user.id),
        "user_role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "user_department": user.department,
        "index": index,
        "storage_context": storage_context,
    }

    async def event_stream():
        state["messages"] = await load_session_history(state["session_id"])
        scope = ScopeClassifier(department=state.get("user_department")).classify(state["query"])
        state["scope_result"] = scope
        scope_decision = evaluate_scope_result(scope, state.get("retrieved_nodes"))
        if scope_decision.get("action") == "out_of_scope":
            answer = "This question appears outside the current knowledge base scope."
            await save_to_session(state["session_id"], state["user_id"], {"role": "user", "content": state["query"]})
            await save_to_session(state["session_id"], state["user_id"], {"role": "assistant", "content": answer})
            yield f"data: {json.dumps({'type':'final','content':answer})}\n\n"
            return

        state["stakes_level"] = classify_stakes(state["query"], state.get("user_role"))
        retriever = get_retriever_for_user(state)
        nodes = await retriever.aretrieve(state["query"])
        nodes, entities = await filter_nodes_by_query_entities(state["query"], nodes)
        nodes = drop_deprecated_nodes(nodes)
        nodes = rerank_by_trust_score(nodes)
        state["retrieved_nodes"] = nodes
        state["query_entities"] = entities
        state["context"] = build_context_string(nodes)

        prompt = build_prompt(state)
        chunks = []
        async for token in answer_stream(prompt):
            if token:
                chunks.append(token)
                yield f"data: {json.dumps({'type':'chunk','content':token})}\n\n"

        answer = "".join(chunks)
        state["answer"] = answer
        state["citations"] = extract_citations(nodes)
        state["confidence"] = 0.5
        state["confidence_gate_passed"] = confidence_gate(state["confidence"])

        await save_to_session(state["session_id"], state["user_id"], {"role": "user", "content": state["query"]})
        await save_to_session(
            state["session_id"],
            state["user_id"],
            {
                "role": "assistant",
                "content": answer,
                "citations": state["citations"],
                "confidence": state["confidence"],
                "stakes_level": state["stakes_level"],
            },
        )

        yield f"data: {json.dumps({'type':'final','citations':state['citations'], 'confidence':state['confidence'], 'stakes_level':state['stakes_level'], 'gap_ticket_id':state.get('gap_ticket_id')})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.get("/sessions")
async def list_sessions(user=Depends(get_current_user)):
    return await list_sessions_store(str(user.id))


@router.delete("/sessions/{session_id}")
async def delete_session(session_id: str, user=Depends(get_current_user)):
    deleted = await delete_session_store(session_id, str(user.id))
    if not deleted:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return {"deleted": True, "session_id": session_id}
