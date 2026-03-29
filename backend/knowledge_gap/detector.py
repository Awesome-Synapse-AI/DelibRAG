import json
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from llama_index.llms.openai import OpenAI

from config import get_settings
from agent.state import AgentState


CONTRADICTION_DETECTION_PROMPT = """
You are checking retrieved documents for contradictions.
Query: {query}

Documents:
{docs}

Return strict JSON:
{{
  "has_contradiction": true/false,
  "description": "short description",
  "sources": ["source_1", "source_2"]
}}
"""


@dataclass
class GapDetector:
    retrieval_score_threshold: Optional[float] = None
    confidence_threshold: Optional[float] = None
    llm_model: str = "gpt-5-nano"

    def __post_init__(self):
        settings = get_settings()
        if self.retrieval_score_threshold is None:
            self.retrieval_score_threshold = settings.gap_retrieval_score_threshold
        if self.confidence_threshold is None:
            self.confidence_threshold = settings.gap_confidence_threshold
        self._llm = OpenAI(model=self.llm_model)

    async def check_gap(self, state: AgentState) -> Optional[Dict[str, Any]]:
        nodes = state.get("retrieved_nodes") or []
        query = state.get("query", "")

        # Condition A: No relevant docs.
        max_score = max((self._node_score(n) for n in nodes), default=0.0)
        if not nodes or max_score < self.retrieval_score_threshold:
            return self._build_gap_ticket(
                state,
                gap_type="missing_knowledge",
                description=f"No relevant documents found for: {query}",
            )

        # Condition B: Contradiction detection.
        contradiction = await self._detect_contradiction(nodes, query)
        if contradiction:
            return self._build_gap_ticket(
                state,
                gap_type="contradiction",
                description=contradiction.get("description", "Contradiction detected in retrieved evidence"),
                conflicting_sources=contradiction.get("sources", []),
            )

        # Condition C: Low confidence on in-scope query.
        in_scope = bool(state.get("in_scope"))
        confidence = float(state.get("confidence", 1.0))
        if in_scope and confidence < self.confidence_threshold:
            return self._build_gap_ticket(
                state,
                gap_type="low_confidence",
                description=(
                    f"LLM confidence ({confidence:.2f}) is below threshold "
                    f"({self.confidence_threshold:.2f}) for in-scope query: {query}"
                ),
            )

        return None

    async def _detect_contradiction(self, nodes: List[Any], query: str) -> Optional[Dict[str, Any]]:
        top_nodes = nodes[:5]
        prompt = CONTRADICTION_DETECTION_PROMPT.format(
            query=query,
            docs="\n\n".join(self._node_text(n) for n in top_nodes),
        )
        result = await self._llm.acomplete(prompt)
        parsed = self._parse_contradiction_result(getattr(result, "text", str(result)))
        if parsed.get("has_contradiction"):
            if not parsed.get("sources"):
                parsed["sources"] = [self._node_source(n) for n in top_nodes]
            return parsed
        return None

    def _build_gap_ticket(self, state: AgentState, gap_type: str, description: str, **kwargs) -> Dict[str, Any]:
        department = state.get("user_department")
        return {
            "query": state.get("query"),
            "user_id": state.get("user_id"),
            "user_role": state.get("user_role"),
            "department": department,
            "gap_type": gap_type,
            "description": description,
            "suggested_owner": self._suggest_owner(department),
            "status": "open",
            **kwargs,
        }

    def _suggest_owner(self, department: Optional[str]) -> str:
        if not department:
            return "admin"
        normalized = department.strip().lower()
        if normalized in {"clinical", "clinician"}:
            return "clinical-knowledge-owner"
        if normalized in {"management", "manager"}:
            return "manager-knowledge-owner"
        return f"{normalized}-knowledge-owner"

    @staticmethod
    def _node_score(node: Any) -> float:
        if isinstance(node, dict):
            return float(node.get("score", 0.0))
        return float(getattr(node, "score", 0.0) or 0.0)

    @staticmethod
    def _node_text(node: Any) -> str:
        if isinstance(node, dict):
            return str(node.get("text", ""))
        if hasattr(node, "text"):
            return str(getattr(node, "text", ""))
        if hasattr(node, "get_text"):
            return str(node.get_text())
        if hasattr(node, "node") and hasattr(node.node, "get_content"):
            return str(node.node.get_content())
        return str(node)

    @staticmethod
    def _node_source(node: Any) -> str:
        metadata = {}
        if isinstance(node, dict):
            metadata = node.get("metadata", {}) or {}
            return str(metadata.get("source_id") or metadata.get("doc_id") or metadata.get("source") or "unknown")

        if hasattr(node, "metadata"):
            metadata = getattr(node, "metadata", {}) or {}
        elif hasattr(node, "node") and hasattr(node.node, "metadata"):
            metadata = getattr(node.node, "metadata", {}) or {}

        return str(metadata.get("source_id") or metadata.get("doc_id") or metadata.get("source") or "unknown")

    @staticmethod
    def _parse_contradiction_result(text: str) -> Dict[str, Any]:
        try:
            json_start = text.find("{")
            json_end = text.rfind("}")
            if json_start != -1 and json_end != -1:
                return json.loads(text[json_start : json_end + 1])
        except Exception:
            pass
        lowered = text.lower()
        has_contradiction = "true" in lowered and "contradiction" in lowered
        return {
            "has_contradiction": has_contradiction,
            "description": text.strip()[:500],
            "sources": [],
        }
