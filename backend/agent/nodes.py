import asyncio
import uuid
from typing import Any, List
import logging
from pathlib import Path

from llama_index.llms.openai import OpenAI
from langsmith import traceable

from agent.confidence_gate import ConfidenceGate
from agent.memory import load_session_history, save_to_session, generate_and_save_title
from agent.stakes_classifier import StakesClassifier
from agent.state import AgentState
from audit.trail import write_audit_entry
from knowledge_gap.detector import GapDetector
from knowledge_gap.ticket_manager import create_gap_ticket
from retrieval.context_builder import build_context_string
from retrieval.entity_filter import filter_nodes_by_query_entities
from retrieval.hybrid_retriever import build_hybrid_retriever, build_vector_retriever
from retrieval.scope_classifier import (
    ScopeClassifier,
    evaluate_scope_result,
    infer_primary_knowledge_domain,
    user_knowledge_domain_for_gap,
)

logger = logging.getLogger(__name__)


def get_retriever_for_user(state: AgentState):
    index = state.get("index")
    storage_context = state.get("storage_context")
    if index is None or storage_context is None:
        raise ValueError("index and storage_context must be provided in AgentState")
    user = _state_user(state)
    return build_hybrid_retriever(index=index, storage_context=storage_context, user=user)


def _state_user(state: AgentState):
    return type("User", (), {"role": state["user_role"], "department": state["user_department"]})


def rerank_by_trust_score(nodes: List[Any]) -> List[Any]:
    def _score(n):
        meta = _node_metadata(n)
        return float(meta.get("source_trust_score", 1.0))

    return sorted(nodes, key=_score, reverse=True)


def drop_deprecated_nodes(nodes: List[Any]) -> List[Any]:
    active: List[Any] = []
    for n in nodes:
        if _node_metadata(n).get("is_deprecated") is True:
            continue
        active.append(n)
    return active


def extract_citations(nodes: List[Any]) -> List[str]:
    citations: List[str] = []
    for n in nodes:
        meta = _node_metadata(n)
        source = meta.get("doc_id") or meta.get("source_id") or meta.get("source")
        if source:
            citations.append(str(source))
    return citations


def extract_citation_details(nodes: List[Any]) -> List[dict]:
    details: List[dict] = []
    for n in nodes:
        meta = _node_metadata(n)
        source = meta.get("doc_id") or meta.get("source_id") or meta.get("source")
        if not source:
            continue
        source_text = str(source)
        title = str(meta.get("title") or Path(source_text).name or source_text)
        section = str(meta.get("section") or meta.get("chunk_label") or "")
        excerpt = _node_text(n).strip()
        trust_raw = meta.get("source_trust_score", meta.get("trust_score", 1.0))
        try:
            trust_score = float(trust_raw)
        except (TypeError, ValueError):
            trust_score = 1.0
        details.append(
            {
                "source": source_text,
                "title": title,
                "section": section,
                "trust_score": trust_score,
                "excerpt": excerpt,
            }
        )
    return details


def role_mismatch_answer_text(state: AgentState) -> str:
    domain = state.get("role_mismatch_query_domain")

    if domain == "clinical" or domain == "management":
        return "The retrieved information belongs to other roles, so there is no available information to answer your query.  No knowledge-gap ticket has been created."


    return (
        "This question does not match knowledge exposed for your current role. "
        "No knowledge-gap ticket has been created."
    )


def build_prompt(state: AgentState) -> str:
    history = state.get("messages") or []
    rendered = []
    for m in history:
        if isinstance(m, dict):
            rendered.append(f"{m.get('role')}: {m.get('content')}")
        else:
            rendered.append(f"{m.type}: {m.content}")
    history_text = "\n".join(rendered)
    context = state.get("context") or ""
    query = state.get("query") or ""
    stakes = state.get("stakes_level", "medium")
    return (
        "You are a careful assistant. Use only provided evidence.\n\n"
        f"Stakes level: {stakes}\n\n"
        f"Conversation:\n{history_text}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {query}\n\n"
        "Answer with concise reasoning and citations."
    )


@traceable(name="scope_check", run_type="chain")
async def scope_check_node(state: AgentState) -> AgentState:
    classifier = ScopeClassifier(department=state.get("user_department"))
    result = classifier.classify(state["query"])
    state["scope_result"] = result

    # Check role-topic mismatch early — before retrieval and gap detection.
    # This prevents gap tickets from firing when the query belongs to a different role's domain.
    udom = user_knowledge_domain_for_gap(state.get("user_role"), state.get("user_department"))
    if udom:  # skip check for admin (udom=None)
        qdom = await infer_primary_knowledge_domain((state.get("query") or "").strip())
        if qdom and qdom != udom:
            state["role_topic_mismatch"] = True
            state["role_mismatch_query_domain"] = qdom
            return state

    state["role_topic_mismatch"] = False
    state.pop("role_mismatch_query_domain", None)
    return state


@traceable(name="stakes_classify", run_type="chain")
async def stakes_classify_node(state: AgentState) -> AgentState:
    classifier = StakesClassifier()
    classification = await classifier.classify(query=state["query"], user_role=state.get("user_role", ""))
    state["stakes_classification"] = classification
    state["stakes_level"] = classification["stakes_level"]
    return state


async def retrieve_node(state: AgentState) -> AgentState:
    stakes = state.get("stakes_level", "high")
    if stakes == "low":
        return await low_stakes_retrieve_node(state)
    return await high_stakes_retrieve_node(state)


@traceable(name="low_stakes_retrieve", run_type="retriever")
async def low_stakes_retrieve_node(state: AgentState) -> AgentState:
    retriever = get_retriever_for_user(state)
    nodes = await retriever.aretrieve(state["query"])
    nodes, entities = await filter_nodes_by_query_entities(state["query"], nodes)
    nodes = rerank_by_trust_score(drop_deprecated_nodes(nodes))
    state["retrieved_nodes"] = nodes
    state["context"] = build_context_string(nodes)
    state["query_entities"] = entities
    state["raw_vector_max_score"] = await _raw_vector_max_score(state, state["query"])
    return state


@traceable(name="high_stakes_retrieve", run_type="retriever")
async def high_stakes_retrieve_node(state: AgentState) -> AgentState:
    retriever = get_retriever_for_user(state)
    base_query = state["query"]
    tech_query, ops_query = await asyncio.gather(
        _rephrase_query(base_query, "technical"),
        _rephrase_query(base_query, "operational"),
    )
    queries = [base_query, tech_query, ops_query]

    raw_retrievals = await asyncio.gather(*[retriever.aretrieve(q) for q in queries])
    retrievals: List[List[Any]] = []
    for batch in raw_retrievals:
        per_query_nodes = rerank_by_trust_score(drop_deprecated_nodes(list(batch)))
        retrievals.append(per_query_nodes)

    flat_nodes = [node for batch in retrievals for node in batch]
    dedup_nodes = _deduplicate_nodes(flat_nodes)
    dedup_nodes, entities = await filter_nodes_by_query_entities(base_query, dedup_nodes)
    all_nodes = drop_deprecated_nodes(dedup_nodes)

    contradictions = await _detect_all_contradictions(all_nodes, base_query)
    evidence_summary = _build_evidence_summary(all_nodes)

    state["retrieved_nodes"] = all_nodes
    state["context"] = build_context_string(all_nodes)
    state["query_entities"] = entities
    state["raw_vector_max_score"] = await _raw_vector_max_score(state, base_query)
    state["audit_trail"] = {
        "retrieval_queries": queries,
        "sources_considered": extract_citations(all_nodes),
        "contradictions_found": contradictions,
        "evidence_summary": evidence_summary,
        "retrieval_path": {
            "queries_issued": queries,
            "sources_considered": extract_citations(all_nodes),
            "nodes_retrieved": len(flat_nodes),
            "nodes_after_merging": len(all_nodes),
        },
        "alternatives_considered": [tech_query, ops_query],
    }
    return state


def get_llm(max_tokens: int = 2048):
    return OpenAI(model="gpt-5-nano", max_tokens=max_tokens)


async def answer_generate_node(state: AgentState) -> AgentState:
    if state.get("role_topic_mismatch"):
        state["answer"] = role_mismatch_answer_text(state)
        state["citations"] = []
        state["citation_details"] = []
        return state

    llm = get_llm(max_tokens=2048)
    prompt = build_prompt(state)
    response = await llm.acomplete(prompt)
    state["answer"] = getattr(response, "text", str(response))
    nodes = state.get("retrieved_nodes") or []
    state["citations"] = extract_citations(nodes)
    state["citation_details"] = extract_citation_details(nodes)
    return state


@traceable(name="answer_stream", run_type="llm")
async def answer_stream(prompt: str):
    llm = get_llm(max_tokens=2048)
    stream = await llm.astream_complete(prompt)
    async for chunk in stream:
        # Prefer token delta for true incremental streaming.
        delta = getattr(chunk, "delta", None)
        if isinstance(delta, str) and delta:
            yield delta
            continue
        text = getattr(chunk, "text", None)
        if isinstance(text, str) and text:
            yield text


@traceable(name="confidence_check", run_type="chain")
async def confidence_check_node(state: AgentState) -> AgentState:
    if state.get("role_topic_mismatch"):
        state["confidence"] = 1.0
        state["confidence_gate_passed"] = True
        state["requires_human_review"] = False
        return state
    gate = ConfidenceGate()
    return await gate.evaluate(state)


@traceable(name="out_of_scope_response", run_type="chain")
async def out_of_scope_response_node(state: AgentState) -> AgentState:
    state["answer"] = "This question appears outside the current knowledge base scope."
    state["citations"] = []
    state["confidence"] = 1.0
    state["confidence_gate_passed"] = True
    state["requires_human_review"] = False
    return state


async def role_mismatch_response_node(state: AgentState) -> AgentState:
    state["answer"] = role_mismatch_answer_text(state)
    state["citations"] = []
    state["citation_details"] = []
    state["confidence"] = 1.0
    state["confidence_gate_passed"] = True
    state["requires_human_review"] = False
    state["gap_ticket_id"] = None
    state["gap_ticket_preview"] = None
    return state


@traceable(name="gap_detect", run_type="chain")
async def gap_detect_node(state: AgentState) -> AgentState:
    # Preserve first detected gap to keep deterministic precedence:
    # missing_knowledge/contradiction (pre-answer) before low_confidence (post-answer).
    if state.get("gap_ticket_id"):
        return state

    # role_topic_mismatch is already resolved in scope_check_node — respect it here.
    if state.get("role_topic_mismatch"):
        return state

    detector = GapDetector()
    gap_ticket = await detector.check_gap(state)
    if gap_ticket:
        state["gap_ticket_id"] = "pending"
        state["gap_ticket_preview"] = gap_ticket
    return state


@traceable(name="gap_ticket_create", run_type="chain")
async def gap_ticket_create_node(state: AgentState) -> AgentState:
    if state.get("role_topic_mismatch"):
        state.pop("gap_ticket_preview", None)
        if state.get("gap_ticket_id") == "pending":
            state["gap_ticket_id"] = None
        return state
    if state.get("gap_ticket_id") != "pending":
        return state
    payload = state.get("gap_ticket_preview")
    db = state.get("db")
    if not isinstance(payload, dict) or db is None:
        return state

    try:
        ticket = await create_gap_ticket(db, payload)
    except Exception:
        logger.exception("Failed to create knowledge-gap ticket (query=%s)", state.get("query"))
        return state

    state["gap_ticket_id"] = str(ticket.id)
    return state


@traceable(name="audit_log", run_type="chain")
async def audit_log_node(state: AgentState) -> AgentState:
    if state.get("stakes_level") != "high":
        return state

    query_id = state.get("query_id") or str(uuid.uuid4())
    state["query_id"] = query_id
    audit = state.get("audit_trail") or {}
    entry = {
        "session_id": state.get("session_id"),
        "query_id": query_id,
        "user_id": state.get("user_id"),
        "user_role": state.get("user_role"),
        "stakes_classification": state.get("stakes_classification"),
        "retrieval_path": audit.get("retrieval_path"),
        "evidence_weighed": _evidence_items(state.get("retrieved_nodes") or []),
        "contradictions_found": audit.get("contradictions_found", []),
        "alternatives_considered": audit.get("alternatives_considered", []),
        "confidence": state.get("confidence"),
        "confidence_gate_passed": state.get("confidence_gate_passed", False),
        "requires_human_review": state.get("requires_human_review", False),
        "final_answer": state.get("answer"),
        "citations": state.get("citations", []),
    }
    await write_audit_entry(entry)
    return state


@traceable(name="memory_save", run_type="chain")
async def memory_save_node(state: AgentState) -> AgentState:
    session_id = state["session_id"]
    user_id = state["user_id"]
    query = state.get("query")
    answer = state.get("answer")
    
    # Check if this is the first exchange (for title generation)
    history = await load_session_history(session_id, window=100)
    is_first_exchange = len(history) == 0
    
    await save_to_session(
        session_id=session_id,
        user_id=user_id,
        turn={"role": "user", "content": query},
    )
    await save_to_session(
        session_id=session_id,
        user_id=user_id,
        turn={
            "role": "assistant",
            "content": answer,
            "citations": state.get("citations"),
            "citation_details": state.get("citation_details"),
            "confidence": state.get("confidence"),
            "stakes_level": state.get("stakes_level"),
            "gap_ticket_id": state.get("gap_ticket_id"),
            "requires_human_review": state.get("requires_human_review"),
        },
    )
    
    # Generate title for the first exchange
    if is_first_exchange and query and answer:
        await generate_and_save_title(session_id, query, answer)
    
    return state


@traceable(name="load_history", run_type="chain")
async def load_history_node(state: AgentState) -> AgentState:
    history = await load_session_history(state["session_id"])
    state["messages"] = history
    return state


async def scope_gate_node(state: AgentState) -> AgentState:
    result = state.get("scope_result") or {}
    scope_eval = evaluate_scope_result(result, state.get("retrieved_nodes"))
    state["scope_decision"] = scope_eval
    return state


async def _rephrase_query(query: str, angle: str) -> str:
    llm = get_llm(max_tokens=128)
    prompt = (
        f"Rewrite this query from a {angle} perspective while preserving intent. "
        "Return one sentence only.\n\n"
        f"Query: {query}"
    )
    response = await llm.acomplete(prompt)
    text = getattr(response, "text", str(response)).strip()
    return text or query


async def _detect_all_contradictions(nodes: List[Any], query: str) -> List[dict]:
    if not nodes:
        return []
    detector = GapDetector()
    contradiction = await detector._detect_contradiction(nodes, query)  # noqa: SLF001
    if contradiction:
        return [contradiction]
    return []


def _build_evidence_summary(nodes: List[Any], limit: int = 5) -> str:
    lines = []
    for idx, node in enumerate(nodes[:limit], start=1):
        source = _node_metadata(node).get("doc_id") or _node_metadata(node).get("source_id") or "unknown_source"
        text = _node_text(node).strip().replace("\n", " ")
        lines.append(f"{idx}. [{source}] {text[:220]}")
    return "\n".join(lines) if lines else "No evidence retrieved."


def _evidence_items(nodes: List[Any], limit: int = 10) -> List[str]:
    items = []
    for node in nodes[:limit]:
        source = _node_metadata(node).get("doc_id") or _node_metadata(node).get("source_id") or "unknown_source"
        text = _node_text(node).strip().replace("\n", " ")
        items.append(f"[{source}] {text[:180]}")
    return items


def _deduplicate_nodes(nodes: List[Any]) -> List[Any]:
    dedup = {}
    for node in nodes:
        key = _node_id(node)
        if key not in dedup:
            dedup[key] = node
    return list(dedup.values())


def _node_id(node: Any) -> str:
    if hasattr(node, "node") and hasattr(node.node, "node_id"):
        return str(node.node.node_id)
    if hasattr(node, "node_id"):
        return str(node.node_id)
    return str(hash(_node_text(node)))


def _node_text(node: Any) -> str:
    if hasattr(node, "text"):
        return str(getattr(node, "text", ""))
    if hasattr(node, "node") and hasattr(node.node, "get_content"):
        return str(node.node.get_content())
    if hasattr(node, "get_text"):
        return str(node.get_text())
    if isinstance(node, dict):
        return str(node.get("text", ""))
    return str(node)


def _node_metadata(node: Any) -> dict:
    if hasattr(node, "metadata"):
        return getattr(node, "metadata", {}) or {}
    if hasattr(node, "node") and hasattr(node.node, "metadata"):
        return getattr(node.node, "metadata", {}) or {}
    if isinstance(node, dict):
        return node.get("metadata", {}) or {}
    return {}


async def _raw_vector_max_score(state: AgentState, query: str) -> float:
    user = _state_user(state)
    vector_retriever = build_vector_retriever(index=state["index"], user=user, similarity_top_k=10)
    vector_nodes = drop_deprecated_nodes(await vector_retriever.aretrieve(query))
    return max((float(getattr(n, "score", 0.0) or 0.0) for n in vector_nodes), default=0.0)
