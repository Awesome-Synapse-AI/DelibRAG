import json
import re
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
        if state.get("role_topic_mismatch"):
            return None

        nodes = state.get("retrieved_nodes") or []
        query = state.get("query", "")
        stakes = (state.get("stakes_level") or "").lower()

        # Condition A: No relevant docs.
        max_score = max((self._node_score(n) for n in nodes), default=0.0)
        raw_vector_max = float(state.get("raw_vector_max_score", 0.0) or 0.0)
        # Use a composite score because fused/auto-merged scores and raw vector scores
        # live on different scales depending on retriever mode.
        relevance_score = max(max_score, raw_vector_max)

        effective_threshold = float(self.retrieval_score_threshold or 0.0)
        if not nodes or relevance_score < effective_threshold:
            return self._build_gap_ticket(
                state,
                gap_type="missing_knowledge",
                description=(
                    f"No relevant documents found for: {query} "
                    f"(relevance_score={relevance_score:.4f}, "
                    f"fusion_max={max_score:.4f}, vector_max={raw_vector_max:.4f}, "
                    f"threshold={effective_threshold:.4f})"
                ),
            )

        # Condition B: Contradiction detection.
        contradiction = None
        if stakes != "low":
            existing = ((state.get("audit_trail") or {}).get("contradictions_found") or [])
            if existing:
                contradiction = existing[0]
            else:
                contradiction = await self._detect_contradiction(nodes, query)
        if contradiction:
            return self._build_gap_ticket(
                state,
                gap_type="contradiction",
                description=contradiction.get("description", "Contradiction detected in retrieved evidence"),
                conflicting_sources=contradiction.get("sources", []),
            )

        # Condition C: Low confidence on in-scope query.
        scope_result = state.get("scope_result") or {}
        in_scope = bool(state.get("in_scope", scope_result.get("in_scope", False)))
        confidence = float(state.get("confidence", 1.0))
        if stakes != "low" and in_scope and confidence < self.confidence_threshold:
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
        top_nodes = self._select_contradiction_candidates(nodes, query, limit=5)
        if len(top_nodes) < 2:
            return None

        prompt = CONTRADICTION_DETECTION_PROMPT.format(
            query=query,
            docs="\n\n".join(self._node_text(n) for n in top_nodes),
        )
        result = await self._llm.acomplete(prompt)
        parsed = self._parse_contradiction_result(getattr(result, "text", str(result)))
        if parsed.get("has_contradiction"):
            valid_sources = {self._node_source(n) for n in top_nodes}
            normalized_sources = self._normalize_sources(parsed.get("sources", []), valid_sources)
            if len(normalized_sources) < 2:
                # Ignore weak/placeholder outputs like ["source_1", "source_2"].
                return None
            parsed["sources"] = normalized_sources
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

    def _select_contradiction_candidates(self, nodes: List[Any], query: str, limit: int = 5) -> List[Any]:
        query_terms = self._query_keywords(query)
        selected: List[Any] = []
        seen_sources: set[str] = set()

        for node in nodes:
            source = self._node_source(node)
            if source in seen_sources:
                continue
            node_terms = self._query_keywords(self._node_text(node))
            overlap = len(query_terms & node_terms)
            if overlap < 2:
                continue
            selected.append(node)
            seen_sources.add(source)
            if len(selected) >= limit:
                break

        return selected

    @staticmethod
    def _keyword_coverage(query_terms: set[str], text: str) -> float:
        if not query_terms:
            return 0.0
        text_terms = GapDetector._query_keywords(text)
        return len(query_terms & text_terms) / max(len(query_terms), 1)

    @staticmethod
    def _normalize_sources(raw_sources: List[Any], valid_sources: set[str]) -> List[str]:
        normalized: List[str] = []
        for src in raw_sources:
            candidate = str(src)
            if candidate in valid_sources:
                normalized.append(candidate)
        return list(dict.fromkeys(normalized))

    @staticmethod
    def _query_keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", text.lower())
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "about",
            "tell",
            "what",
            "when",
            "where",
            "who",
            "why",
            "how",
            "your",
            "have",
            "has",
            "had",
            "are",
            "was",
            "were",
            "will",
            "would",
            "should",
            "can",
            "could",
            "not",
            "any",
            "all",
        }
        return {tok for tok in tokens if len(tok) >= 3 and tok not in stopwords}

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
