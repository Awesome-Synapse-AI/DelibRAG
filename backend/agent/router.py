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
from langsmith import traceable

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
from agent.tracing import is_tracing_enabled
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


@traceable(run_type="chain", name="DelibRAG_Chat")
async def run_agent_graph(graph, state: AgentState):
    """Wrapper to trace the entire graph execution as a single tree."""
    from langchain_core.runnables import RunnableConfig
    
    config = RunnableConfig(
        run_name="Agent_Graph",
        tags=["chat", f"role_{state.get('user_role', 'unknown')}"],
        metadata={
            "session_id": state.get("session_id"),
            "user_role": state.get("user_role"),
            "user_department": state.get("user_department"),
            "query": state.get("query", "")[:100],
        }
    )
    
    return await graph.ainvoke(state, config=config)


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
    
    # Use traceable wrapper to create a single root trace
    result = await run_agent_graph(graph, state)
    
    return {
        "answer": result.get("answer"),
        "citations": result.get("citations"),
        "citation_details": result.get("citation_details"),
        "confidence": result.get("confidence"),
        "stakes_level": result.get("stakes_level"),
        "gap_ticket_id": result.get("gap_ticket_id"),
        "gap_ticket_created": result.get("gap_ticket_created", False),
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
    import langsmith as ls
    
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
        
        # Create trace inputs
        trace_inputs = {
            "session_id": session_id,
            "query": query,
            "user_role": working_state["user_role"],
            "user_department": working_state["user_department"],
        }
        
        # Execute with or without tracing
        if is_tracing_enabled():
            # Use tracing context to ensure all @traceable decorated functions nest properly
            with ls.tracing_context(
                project_name=settings.langsmith_project,
                tags=["chat", f"role_{working_state['user_role']}"],
                metadata={"session_id": session_id}
            ):
                with ls.trace(
                    name="DelibRAG_Chat_Stream",
                    run_type="chain",
                    inputs=trace_inputs,
                ) as rt:
                    # Execute all nodes - each has @traceable decorator so will appear as child
                    working_state = await load_history_node(working_state)
                    working_state = await scope_check_node(working_state)
                    scope_in = bool((working_state.get("scope_result") or {}).get("in_scope"))

                    if not scope_in:
                        working_state = await out_of_scope_response_node(working_state)
                        final_text = working_state.get("answer", "")
                    else:
                        working_state = await stakes_classify_node(working_state)
                        stakes = working_state.get("stakes_level", "high")
                        
                        if stakes == "low":
                            working_state = await low_stakes_retrieve_node(working_state)
                        else:
                            working_state = await high_stakes_retrieve_node(working_state)
                        
                        # Generate answer (removed premature gap_detect_node call)
                        final_text = ""
                        if working_state.get("role_topic_mismatch"):
                            final_text = role_mismatch_answer_text(working_state)
                            working_state["answer"] = final_text
                            working_state["citations"] = []
                            working_state["citation_details"] = []
                        else:
                            prompt = build_prompt(working_state)
                            async for piece in answer_stream(prompt):
                                if piece:
                                    if piece.startswith(final_text):
                                        final_text = piece
                                    else:
                                        final_text += piece
                            working_state["answer"] = final_text
                            nodes = working_state.get("retrieved_nodes") or []
                            working_state["citations"] = extract_citations(nodes)
                            working_state["citation_details"] = extract_citation_details(nodes)
                        
                        # Post-processing - gap detection happens AFTER answer generation
                        working_state = await confidence_check_node(working_state)
                        working_state = await gap_detect_node(working_state)
                        working_state = await gap_ticket_create_node(working_state)
                        working_state = await audit_log_node(working_state)
                        working_state = await memory_save_node(working_state)
                    
                    # Set trace outputs
                    rt.end(outputs={
                        "answer": working_state.get("answer", ""),
                        "citations": working_state.get("citations", []),
                        "confidence": working_state.get("confidence"),
                        "stakes_level": working_state.get("stakes_level"),
                        "gap_ticket_id": working_state.get("gap_ticket_id"),
                        "gap_ticket_created": working_state.get("gap_ticket_created", False),
                        "requires_human_review": working_state.get("requires_human_review"),
                    })
        else:
            # Execute without tracing
            working_state = await load_history_node(working_state)
            working_state = await scope_check_node(working_state)
            scope_in = bool((working_state.get("scope_result") or {}).get("in_scope"))

            if not scope_in:
                working_state = await out_of_scope_response_node(working_state)
                final_text = working_state.get("answer", "")
            else:
                working_state = await stakes_classify_node(working_state)
                stakes = working_state.get("stakes_level", "high")
                
                if stakes == "low":
                    working_state = await low_stakes_retrieve_node(working_state)
                else:
                    working_state = await high_stakes_retrieve_node(working_state)
                
                # Generate answer (removed premature gap_detect_node call)
                final_text = ""
                if working_state.get("role_topic_mismatch"):
                    final_text = role_mismatch_answer_text(working_state)
                    working_state["answer"] = final_text
                    working_state["citations"] = []
                    working_state["citation_details"] = []
                else:
                    prompt = build_prompt(working_state)
                    async for piece in answer_stream(prompt):
                        if piece:
                            if piece.startswith(final_text):
                                final_text = piece
                            else:
                                final_text += piece
                    working_state["answer"] = final_text
                    nodes = working_state.get("retrieved_nodes") or []
                    working_state["citations"] = extract_citations(nodes)
                    working_state["citation_details"] = extract_citation_details(nodes)
                
                # Post-processing - gap detection happens AFTER answer generation
                working_state = await confidence_check_node(working_state)
                working_state = await gap_detect_node(working_state)
                working_state = await gap_ticket_create_node(working_state)
                working_state = await audit_log_node(working_state)
                working_state = await memory_save_node(working_state)
        
        # Stream the answer to client
        if final_text:
            # Stream in chunks
            chunk_size = 50
            for i in range(0, len(final_text), chunk_size):
                chunk = final_text[i:i+chunk_size]
                yield f"data: {json.dumps({'type':'chunk','content':chunk})}\n\n"

        # Send final message
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
                    "gap_ticket_created": working_state.get("gap_ticket_created", False),
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


class UpdateSessionTitleRequest(BaseModel):
    title: str


@router.patch("/sessions/{session_id}/title")
async def update_session_title(
    session_id: str,
    payload: UpdateSessionTitleRequest,
    user=Depends(get_current_user)
):
    from agent.memory import update_session_title as update_title_store
    
    success = await update_title_store(session_id, str(user.id), payload.title)
    if not success:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Session not found")
    return {"updated": True, "session_id": session_id, "title": payload.title}
