from typing import Any, List, Optional, TypedDict


class RetrievedNode(TypedDict, total=False):
    text: str
    score: float
    source: str
    metadata: dict


class AgentState(TypedDict, total=False):
    query: str
    user_id: str
    user_role: str
    user_department: Optional[str]
    in_scope: bool
    confidence: float
    retrieved_nodes: List[Any]
