import json
import re
from dataclasses import dataclass
from typing import Dict

from llama_index.llms.openai import OpenAI

from config import get_settings

settings = get_settings()
CONFIDENCE_THRESHOLDS = {
    "low": settings.confidence_threshold_low,
    "high": settings.confidence_threshold_high,
}


CONFIDENCE_PROMPT = """
Score confidence for the proposed answer on a 0.0-1.0 scale using ONLY provided evidence.
Return JSON only with numeric confidence in [0,1]:
{{"confidence": 0.0}}

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
        threshold = CONFIDENCE_THRESHOLDS.get(stakes, 0.75)

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
        score = self._extract_confidence_value(text)
        if score is None:
            # Avoid pathological 0% when model formatting is imperfect.
            score = 0.5
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

    @classmethod
    def _extract_confidence_value(cls, text: str) -> float | None:
        parsed = cls._parse_json(text)
        value = parsed.get("confidence")
        if value is not None:
            normalized = cls._normalize_confidence(value)
            if normalized is not None:
                return normalized

        key_match = re.search(r'"confidence"\s*:\s*([0-9]+(?:\.[0-9]+)?)(%?)', text, re.IGNORECASE)
        if key_match:
            number = float(key_match.group(1))
            if key_match.group(2) == "%" or number > 1.0:
                number = number / 100.0
            return number

        bare_match = re.search(r"\b([0-9]+(?:\.[0-9]+)?)\s*%?\b", text)
        if bare_match:
            number = float(bare_match.group(1))
            if number > 1.0:
                number = number / 100.0
            return number
        return None

    @staticmethod
    def _normalize_confidence(value) -> float | None:
        try:
            if isinstance(value, str):
                cleaned = value.strip().replace("%", "")
                number = float(cleaned)
                if "%" in value or number > 1.0:
                    number = number / 100.0
                return number
            number = float(value)
            if number > 1.0:
                number = number / 100.0
            return number
        except (TypeError, ValueError):
            return None

    def _build_uncertain_response(self, state: dict, confidence: float, threshold: float) -> str:
        original_answer = (state.get("answer") or "").strip()
        header = (
            "WARNING: Confidence below threshold "
            f"({confidence:.0%} < {threshold:.0%}). "
            "Human review is recommended."
        )
        if original_answer:
            return f"{header}\n\n{original_answer}"
        return (
            f"{header}\n\n"
            "No answer content was generated. Please request human review."
        )


def confidence_gate(confidence: float, minimum: float = 0.45) -> bool:
    """Backward-compatible helper used by older code paths."""
    return float(confidence) >= float(minimum)
