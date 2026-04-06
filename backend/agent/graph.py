from langgraph.graph import END, StateGraph

from agent.nodes import (
    answer_generate_node,
    audit_log_node,
    confidence_check_node,
    gap_detect_node,
    high_stakes_retrieve_node,
    load_history_node,
    low_stakes_retrieve_node,
    medium_stakes_retrieve_node,
    memory_save_node,
    out_of_scope_response_node,
    scope_check_node,
    stakes_classify_node,
)
from agent.state import AgentState


def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("load_history", load_history_node)
    graph.add_node("scope_check", scope_check_node)
    graph.add_node("stakes_classify", stakes_classify_node)
    graph.add_node("low_stakes_retrieve", low_stakes_retrieve_node)
    graph.add_node("medium_stakes_retrieve", medium_stakes_retrieve_node)
    graph.add_node("high_stakes_retrieve", high_stakes_retrieve_node)
    graph.add_node("gap_detect", gap_detect_node)
    graph.add_node("answer_generate", answer_generate_node)
    graph.add_node("confidence_check", confidence_check_node)
    graph.add_node("audit_log", audit_log_node)
    graph.add_node("out_of_scope_response", out_of_scope_response_node)
    graph.add_node("memory_save", memory_save_node)

    graph.set_entry_point("load_history")
    graph.add_edge("load_history", "scope_check")
    def route_after_scope(state: AgentState):
        scope = (state.get("scope_result") or {}).get("in_scope")
        if not scope:
            return "out_of_scope_response"
        return "stakes_classify"

    graph.add_conditional_edges(
        "scope_check",
        route_after_scope,
        {
            "out_of_scope_response": "out_of_scope_response",
            "stakes_classify": "stakes_classify",
        },
    )

    def route_after_stakes(state: AgentState):
        stakes = state.get("stakes_level", "medium")
        if stakes == "low":
            return "low_stakes_retrieve"
        if stakes == "high":
            return "high_stakes_retrieve"
        return "medium_stakes_retrieve"

    graph.add_conditional_edges(
        "stakes_classify",
        route_after_stakes,
        {
            "low_stakes_retrieve": "low_stakes_retrieve",
            "medium_stakes_retrieve": "medium_stakes_retrieve",
            "high_stakes_retrieve": "high_stakes_retrieve",
        },
    )

    def route_after_retrieve(state: AgentState):
        from retrieval.scope_classifier import evaluate_scope_result

        decision = evaluate_scope_result(state.get("scope_result") or {}, state.get("retrieved_nodes")).get("action")
        if decision == "in_scope_gap":
            return "gap_detect"
        return "answer_generate"

    graph.add_conditional_edges(
        "low_stakes_retrieve",
        route_after_retrieve,
        {
            "gap_detect": "gap_detect",
            "answer_generate": "answer_generate",
        },
    )
    graph.add_conditional_edges(
        "medium_stakes_retrieve",
        route_after_retrieve,
        {
            "gap_detect": "gap_detect",
            "answer_generate": "answer_generate",
        },
    )
    graph.add_conditional_edges(
        "high_stakes_retrieve",
        route_after_retrieve,
        {
            "gap_detect": "gap_detect",
            "answer_generate": "answer_generate",
        },
    )

    graph.add_edge("gap_detect", "answer_generate")
    graph.add_edge("answer_generate", "confidence_check")
    graph.add_edge("confidence_check", "audit_log")
    graph.add_edge("audit_log", "memory_save")
    graph.add_edge("out_of_scope_response", "audit_log")
    graph.add_edge("memory_save", END)

    return graph.compile()
