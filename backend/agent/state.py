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
    stakes_level: Optional[str]
    retrieved_nodes: Optional[list]
    context: Optional[str]
    answer: Optional[str]
    confidence: Optional[float]
    citations: Optional[list[str]]
    audit_trail: Optional[dict]
    gap_ticket_id: Optional[str]
    index: Optional[Any]
    storage_context: Optional[Any]
