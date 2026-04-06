import asyncio
import uuid
from typing import Any, List

from llama_index.llms.openai import OpenAI

from agent.confidence_gate import ConfidenceGate
from agent.memory import load_session_history, save_to_session
from agent.stakes_classifier import StakesClassifier
from agent.state import AgentState
from audit.trail import write_audit_entry
from knowledge_gap.detector import GapDetector
from retrieval.context_builder import build_context_string
from retrieval.entity_filter import filter_nodes_by_query_entities
from retrieval.hybrid_retriever import build_hybrid_retriever, build_vector_retriever
from retrieval.scope_classifier import ScopeClassifier, evaluate_scope_result


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


async def scope_check_node(state: AgentState) -> AgentState:
    classifier = ScopeClassifier(department=state.get("user_department"))
    result = classifier.classify(state["query"])
    state["scope_result"] = result
    return state


async def stakes_classify_node(state: AgentState) -> AgentState:
    classifier = StakesClassifier()
    classification = await classifier.classify(query=state["query"], user_role=state.get("user_role", ""))
    state["stakes_classification"] = classification
    state["stakes_level"] = classification["stakes_level"]
    return state


async def retrieve_node(state: AgentState) -> AgentState:
    stakes = state.get("stakes_level", "medium")
    if stakes == "low":
        return await low_stakes_retrieve_node(state)
    if stakes == "high":
        return await high_stakes_retrieve_node(state)
    return await medium_stakes_retrieve_node(state)


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


async def medium_stakes_retrieve_node(state: AgentState) -> AgentState:
    retriever = get_retriever_for_user(state)
    nodes = await retriever.aretrieve(state["query"])
    nodes, entities = await filter_nodes_by_query_entities(state["query"], nodes)
    nodes = rerank_by_trust_score(drop_deprecated_nodes(nodes))
    state["retrieved_nodes"] = nodes
    state["context"] = build_context_string(nodes)
    state["query_entities"] = entities
    state["raw_vector_max_score"] = await _raw_vector_max_score(state, state["query"])
    return state


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


def get_llm():
    return OpenAI(model="gpt-5-nano")


async def answer_generate_node(state: AgentState) -> AgentState:
    llm = get_llm()
    prompt = build_prompt(state)
    response = await llm.acomplete(prompt)
    state["answer"] = getattr(response, "text", str(response))
    state["citations"] = extract_citations(state.get("retrieved_nodes") or [])
    return state


async def answer_stream(prompt: str):
    llm = get_llm()
    async for chunk in llm.astream_complete(prompt):
        yield getattr(chunk, "text", str(chunk))


async def confidence_check_node(state: AgentState) -> AgentState:
    gate = ConfidenceGate()
    return await gate.evaluate(state)


async def out_of_scope_response_node(state: AgentState) -> AgentState:
    state["answer"] = "This question appears outside the current knowledge base scope."
    state["citations"] = []
    state["confidence"] = 1.0
    state["confidence_gate_passed"] = True
    state["requires_human_review"] = False
    return state


async def gap_detect_node(state: AgentState) -> AgentState:
    detector = GapDetector()
    gap_ticket = await detector.check_gap(state)
    if gap_ticket:
        state["gap_ticket_id"] = "pending"
        state["gap_ticket_preview"] = gap_ticket
    return state


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


async def memory_save_node(state: AgentState) -> AgentState:
    await save_to_session(
        session_id=state["session_id"],
        user_id=state["user_id"],
        turn={"role": "user", "content": state.get("query")},
    )
    await save_to_session(
        session_id=state["session_id"],
        user_id=state["user_id"],
        turn={
            "role": "assistant",
            "content": state.get("answer"),
            "citations": state.get("citations"),
            "confidence": state.get("confidence"),
            "stakes_level": state.get("stakes_level"),
        },
    )
    return state


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
    llm = get_llm()
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
