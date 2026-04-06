from typing import Any, List

from llama_index.llms.openai import OpenAI

from agent.confidence_gate import confidence_gate
from agent.memory import load_session_history, save_to_session
from agent.stakes_classifier import classify_stakes
from agent.state import AgentState
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
    user = type("User", (), {"role": state["user_role"], "department": state["user_department"]})
    return build_hybrid_retriever(index, storage_context, user)


def rerank_by_trust_score(nodes: List[Any]) -> List[Any]:
    def _score(n):
        meta = {}
        if hasattr(n, "metadata"):
            meta = n.metadata or {}
        elif hasattr(n, "node") and hasattr(n.node, "metadata"):
            meta = n.node.metadata or {}
        return float(meta.get("source_trust_score", 1.0))

    return sorted(nodes, key=_score, reverse=True)


def drop_deprecated_nodes(nodes: List[Any]) -> List[Any]:
    active: List[Any] = []
    for n in nodes:
        meta = {}
        if hasattr(n, "metadata"):
            meta = n.metadata or {}
        elif hasattr(n, "node") and hasattr(n.node, "metadata"):
            meta = n.node.metadata or {}
        if meta.get("is_deprecated") is True:
            continue
        active.append(n)
    return active


def extract_citations(nodes: List[Any]) -> List[str]:
    citations: List[str] = []
    for n in nodes:
        meta = {}
        if hasattr(n, "metadata"):
            meta = n.metadata or {}
        elif hasattr(n, "node") and hasattr(n.node, "metadata"):
            meta = n.node.metadata or {}
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
    return (
        "You are a helpful assistant. Use the context to answer.\n\n"
        f"Conversation:\n{history_text}\n\n"
        f"Context:\n{context}\n\n"
        f"Question: {query}\n\n"
        "Answer:"
    )


async def scope_check_node(state: AgentState) -> AgentState:
    classifier = ScopeClassifier(department=state.get("user_department"))
    result = classifier.classify(state["query"])
    state["scope_result"] = result
    return state


async def stakes_classify_node(state: AgentState) -> AgentState:
    state["stakes_level"] = classify_stakes(state["query"], state.get("user_role"))
    return state


async def retrieve_node(state: AgentState) -> AgentState:
    retriever = get_retriever_for_user(state)
    nodes = await retriever.aretrieve(state["query"])
    nodes, entities = await filter_nodes_by_query_entities(state["query"], nodes)
    nodes = drop_deprecated_nodes(nodes)
    nodes = rerank_by_trust_score(nodes)
    state["retrieved_nodes"] = nodes
    state["context"] = build_context_string(nodes)
    state["query_entities"] = entities
    user = type("User", (), {"role": state["user_role"], "department": state["user_department"]})
    vector_retriever = build_vector_retriever(index=state["index"], user=user, similarity_top_k=10)
    vector_nodes = drop_deprecated_nodes(await vector_retriever.aretrieve(state["query"]))
    state["raw_vector_max_score"] = max((float(getattr(n, "score", 0.0) or 0.0) for n in vector_nodes), default=0.0)
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
    confidence = state.get("confidence")
    if confidence is None:
        confidence = 0.5
    state["confidence"] = confidence
    state["confidence_gate_passed"] = confidence_gate(confidence)
    return state


async def out_of_scope_response_node(state: AgentState) -> AgentState:
    state["answer"] = "This question appears outside the current knowledge base scope."
    state["citations"] = []
    return state


async def gap_detect_node(state: AgentState) -> AgentState:
    detector = GapDetector()
    gap_ticket = await detector.check_gap(state)
    if gap_ticket:
        state["gap_ticket_id"] = "pending"
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
