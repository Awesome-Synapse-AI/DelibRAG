import json
from dataclasses import dataclass

from llama_index.llms.openai import OpenAI


STAKES_PROMPT = """
Classify query stakes strictly as LOW or HIGH.

Inputs:
- user_role: {role}
- query: {query}

Return strict JSON only:
{{"stakes_level":"low"}} or {{"stakes_level":"high"}}

Guidelines:
- HIGH if wrong answer could cause security, legal, compliance, production, customer-data,
  major financial/operational impact, or role-sensitive executive decision risk.
- LOW for routine informational/internal guidance with limited downside.
- If uncertain, choose HIGH.
"""


@dataclass
class StakesClassifier:
    llm_model: str = "gpt-5-nano"

    def __post_init__(self):
        self._llm = OpenAI(model=self.llm_model)

    async def classify(self, query: str, user_role: str) -> dict:
        response = await self._llm.acomplete(
            STAKES_PROMPT.format(role=user_role or "unknown", query=query or "")
        )
        text = getattr(response, "text", str(response))
        stakes_level = self._parse_stakes_level(text)
        return {
            "stakes_level": stakes_level,
            "query_complexity": None,
            "role_sensitivity": None,
            "consequence_severity": None,
        }

    @staticmethod
    def _parse_stakes_level(text: str) -> str:
        try:
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1:
                payload = json.loads(text[start : end + 1])
                level = str(payload.get("stakes_level", "")).lower().strip()
                if level in {"low", "high"}:
                    return level
        except Exception:
            pass
        lowered = text.lower()
        if "high" in lowered:
            return "high"
        return "low"


async def classify_stakes(query: str, user_role: str | None = None) -> str:
    """Backward-compatible helper."""
    return (await StakesClassifier().classify(query=query, user_role=user_role or ""))["stakes_level"]
