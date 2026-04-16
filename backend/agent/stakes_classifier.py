import json
from dataclasses import dataclass

from llama_index.llms.openai import OpenAI


STAKES_PROMPT = """
Classify query stakes as LOW or HIGH based on potential consequences of incorrect information.

Inputs:
- user_role: {role}
- query: {query}

Return strict JSON only:
{{"stakes_level":"low"}} or {{"stakes_level":"high"}}

HIGH-STAKES INDICATORS:

Clinical/Healthcare Context:
- Emergency/acute care protocols (MI, stroke, sepsis, respiratory failure, trauma)
- Critical vital sign thresholds and immediate interventions
- Medication dosing for high-risk drugs or emergency situations
- Life-threatening conditions requiring urgent action
- ICU-level care decisions
- Surgical/procedural protocols with serious complications
- Anticoagulation reversal, vasopressor management
- Intubation criteria, ventilator settings
- Neurological emergencies (ICP management, herniation)
- Septic shock management, fluid resuscitation protocols

Management/Business Context:
- Strategic partnerships with significant financial commitment (>$1M annually)
- M&A decisions, major investments, or equity transactions
- Legal compliance, regulatory violations, or litigation risks
- Data security breaches, privacy violations, or IP disputes
- Executive-level decisions affecting company direction
- Contractual obligations with material financial impact
- Competitive threats or market positioning changes
- Board-level governance or fiduciary responsibilities
- Financial reporting, audit findings, or fraud risks
- Crisis management or reputational damage scenarios

LOW-STAKES INDICATORS:

Clinical/Healthcare Context:
- General wellness advice (hydration, nutrition, exercise, sleep)
- Preventive care recommendations (routine checkups, screenings)
- Basic hygiene practices
- Lifestyle modifications for healthy individuals
- Non-urgent health education
- Routine appointment scheduling
- General health information without immediate action needed

Management/Business Context:
- Routine scheduling (meetings, conference rooms, breaks)
- Internal documentation formatting and style preferences
- Team coordination and communication norms
- Casual workplace policies (dress code, break times)
- General planning templates and guidelines
- Non-binding recommendations or suggestions
- Internal process improvements with low financial impact
- Informational queries about standard procedures

DECISION RULES:
1. If wrong answer could cause: death, serious injury, major financial loss (>$100K), legal liability, regulatory violation, security breach, or irreversible harm → HIGH
2. If query involves: emergency protocols, critical thresholds, time-sensitive interventions, executive decisions, strategic commitments, compliance requirements → HIGH
3. If query is about: routine operations, general information, preferences, scheduling, formatting, wellness tips, or has easily reversible consequences → LOW
4. Role alone does NOT determine stakes - a manager asking about meeting room booking is LOW; a clinician asking about daily wellness is LOW
5. When uncertain and no explicit high-risk signals present → choose LOW

Examples:
- "What are the protocols for managing acute MI with hypotension?" → HIGH (emergency, life-threatening)
- "How should I book a conference room for team meetings?" → LOW (routine scheduling)
- "What are the critical BP thresholds for hypertensive emergency?" → HIGH (emergency protocols)
- "What are tips for staying hydrated during the day?" → LOW (general wellness)
- "What are the terms of our AWS strategic partnership?" → HIGH (major financial commitment)
- "When should we schedule our weekly status meetings?" → LOW (routine planning)
"""


@dataclass
class StakesClassifier:
    llm_model: str = "gpt-4o-mini"

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
