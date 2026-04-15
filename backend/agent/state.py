from typing import Annotated, Any, Optional, TypedDict

from langchain_core.messages import BaseMessage


class AgentState(TypedDict, total=False):
    messages: Annotated[list[BaseMessage], "chat history"]
    session_id: str
    user_id: str
    user_role: str
    user_department: str
    query: str
    scope_result: Optional[dict]
    in_scope: Optional[bool]
    stakes_level: Optional[str]
    retrieved_nodes: Optional[list]
    context: Optional[str]
    answer: Optional[str]
    confidence: Optional[float]
    citations: Optional[list[str]]
    citation_details: Optional[list[dict]]
    audit_trail: Optional[dict]
    gap_ticket_id: Optional[str]
    gap_ticket_preview: Optional[dict]
    query_entities: Optional[list[str]]
    stakes_classification: Optional[dict]
    query_id: Optional[str]
    requires_human_review: Optional[bool]
    confidence_gate_passed: Optional[bool]
    raw_vector_max_score: Optional[float]
    index: Optional[Any]
    storage_context: Optional[Any]
    db: Optional[Any]
    role_topic_mismatch: Optional[bool]
    role_mismatch_query_domain: Optional[str]
