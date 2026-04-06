import json
from dataclasses import dataclass
from typing import Dict

from llama_index.llms.openai import OpenAI


CONFIDENCE_THRESHOLDS = {
    "low": 0.0,
    "medium": 0.55,
    "high": 0.75,
}


CONFIDENCE_PROMPT = """
Score confidence for the proposed answer on a 0.0-1.0 scale using ONLY provided evidence.
Return strict JSON: {{"confidence": 0.0}}

Query:
{query}

Answer:
{answer}

Evidence context:
{context}
"""


@dataclass
class ConfidenceGate:
    llm_model: str = "gpt-5-nano"

    def __post_init__(self):
        self._llm = OpenAI(model=self.llm_model)

    async def evaluate(self, state: dict) -> dict:
        stakes = state.get("stakes_level", "medium")
        threshold = CONFIDENCE_THRESHOLDS.get(stakes, 0.55)

        if stakes == "low":
            state["confidence"] = 1.0
            state["confidence_gate_passed"] = True
            state["requires_human_review"] = False
            return state

        confidence = await self._llm_confidence_score(
            query=state.get("query", ""),
            context=state.get("context", ""),
            answer=state.get("answer", ""),
        )
        state["confidence"] = confidence
        state["confidence_gate_passed"] = confidence >= threshold
        state["requires_human_review"] = confidence < threshold

        if confidence < threshold:
            state["answer"] = self._build_uncertain_response(state, confidence, threshold)
        return state

    async def _llm_confidence_score(self, query: str, context: str, answer: str) -> float:
        response = await self._llm.acomplete(
            CONFIDENCE_PROMPT.format(query=query, context=context, answer=answer)
        )
        text = getattr(response, "text", str(response))
        parsed = self._parse_json(text)
        value = parsed.get("confidence", 0.0)
        try:
            score = float(value)
        except (TypeError, ValueError):
            score = 0.0
        return max(0.0, min(1.0, score))

    @staticmethod
    def _parse_json(text: str) -> Dict:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                return json.loads(text[start : end + 1])
        except Exception:
            pass
        return {}

    def _build_uncertain_response(self, state: dict, confidence: float, threshold: float) -> str:
        evidence_summary = (state.get("audit_trail") or {}).get("evidence_summary", "No evidence summary available.")
        return (
            f"WARNING: Confidence below threshold ({confidence:.0%} < {threshold:.0%}).\n\n"
            f"Based on available documentation, evidence found:\n\n{evidence_summary}\n\n"
            "A human should make the final decision. Documentation may be incomplete or contradictory."
        )


def confidence_gate(confidence: float, minimum: float = 0.45) -> bool:
    """Backward-compatible helper used by older code paths."""
    return float(confidence) >= float(minimum)
