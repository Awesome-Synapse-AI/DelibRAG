from langgraph.graph import END, StateGraph

from agent.nodes import (
    answer_generate_node,
    confidence_check_node,
    gap_detect_node,
    load_history_node,
    memory_save_node,
    out_of_scope_response_node,
    retrieve_node,
    scope_check_node,
    stakes_classify_node,
)
from agent.state import AgentState


def build_agent_graph():
    graph = StateGraph(AgentState)

    graph.add_node("load_history", load_history_node)
    graph.add_node("scope_check", scope_check_node)
    graph.add_node("stakes_classify", stakes_classify_node)
    graph.add_node("retrieve", retrieve_node)
    graph.add_node("gap_detect", gap_detect_node)
    graph.add_node("answer_generate", answer_generate_node)
    graph.add_node("confidence_check", confidence_check_node)
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

    graph.add_edge("stakes_classify", "retrieve")

    def route_after_retrieve(state: AgentState):
        from retrieval.scope_classifier import evaluate_scope_result

        decision = evaluate_scope_result(state.get("scope_result") or {}, state.get("retrieved_nodes")).get("action")
        if decision == "in_scope_gap":
            return "gap_detect"
        return "answer_generate"

    graph.add_conditional_edges(
        "retrieve",
        route_after_retrieve,
        {
            "gap_detect": "gap_detect",
            "answer_generate": "answer_generate",
        },
    )

    graph.add_edge("gap_detect", "answer_generate")
    graph.add_edge("answer_generate", "confidence_check")
    graph.add_edge("confidence_check", "memory_save")
    graph.add_edge("out_of_scope_response", "memory_save")
    graph.add_edge("memory_save", END)

    return graph.compile()
