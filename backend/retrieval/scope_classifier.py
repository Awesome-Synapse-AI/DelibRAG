import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Literal, Optional, Sequence

import joblib

logger = logging.getLogger(__name__)

_DOMAIN_CLASSIFY_PROMPT = """Classify the following query into exactly one knowledge domain.

Domains:
- "clinical": medical care, patient treatment, clinical guidelines, diagnoses, medications, wellness, health protocols, nursing, therapy
- "management": business operations, budgets, staffing, scheduling, HR, administration, strategy, reporting, compliance management
- "ambiguous": cannot be clearly assigned to either domain

Return strict JSON only, no explanation:
{{"domain": "clinical"}} or {{"domain": "management"}} or {{"domain": "ambiguous"}}

Query: {query}"""

# Keyword sets used as a last-resort fallback when both LDA and LLM are unavailable.
_CLINICAL_KEYWORDS = {
    "patient", "clinical", "diagnosis", "medication", "treatment", "therapy",
    "symptom", "disease", "nurse", "nursing", "physician", "doctor", "medical",
    "wellness", "health", "guideline", "protocol", "dosage", "prescription",
    "surgery", "ward", "icu", "triage", "vital", "blood", "cardiac", "respiratory",
    "infection", "wound", "rehabilitation", "discharge", "admission", "allergy",
}
_MANAGEMENT_KEYWORDS = {
    "budget", "revenue", "staffing", "schedule", "hr", "hiring", "payroll",
    "compliance", "audit", "strategy", "kpi", "performance", "report", "forecast",
    "procurement", "vendor", "contract", "policy", "operations", "management",
    "administration", "headcount", "onboarding", "offboarding", "expense",
}


def _keyword_infer_domain(query: str) -> Optional[Literal["clinical", "management"]]:
    tokens = set(re.findall(r"[a-z]+", query.lower()))
    clinical_hits = len(tokens & _CLINICAL_KEYWORDS)
    management_hits = len(tokens & _MANAGEMENT_KEYWORDS)
    if clinical_hits > management_hits:
        return "clinical"
    if management_hits > clinical_hits:
        return "management"
    return None


async def _llm_infer_domain(query: str) -> Optional[Literal["clinical", "management"]]:
    """LLM-based fallback for domain inference when LDA models are unavailable."""
    try:
        from llama_index.llms.openai import OpenAI  # local import to avoid circular deps
        llm = OpenAI(model="gpt-5-nano", max_tokens=32)
        response = await llm.acomplete(_DOMAIN_CLASSIFY_PROMPT.format(query=query))
        text = getattr(response, "text", str(response))
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            payload = json.loads(text[start:end + 1])
            domain = str(payload.get("domain", "")).strip().lower()
            if domain in {"clinical", "management"}:
                return domain  # type: ignore[return-value]
    except Exception:
        logger.warning("LLM domain inference failed for query=%r", query, exc_info=True)
    return None


class ScopeClassifier:
    def __init__(
        self,
        manifest_path: str = "scope_manifest.json",
        lda_path: str = "lda_model.pkl",
        vec_path: str = "lda_vectorizer.pkl",
        threshold: float = 0.15,
        department: Optional[str] = None,
    ):
        manifest_file, lda_file, vec_file = self._resolve_files(
            manifest_path=manifest_path,
            lda_path=lda_path,
            vec_path=vec_path,
            department=department,
        )

        self.threshold = threshold
        self.enabled = all([manifest_file, lda_file, vec_file])

        if not self.enabled:
            self.manifest = {"topics": []}
            self.lda = None
            self.vectorizer = None
            logger.warning(
                "ScopeClassifier disabled: missing model artifacts. "
                "Set scope files in /app/scripts/lda_domains/{clinical|manager}/."
            )
            return

        with manifest_file.open("r", encoding="utf-8") as f:
            self.manifest = json.load(f)

        self.lda = joblib.load(lda_file)
        self.vectorizer = joblib.load(vec_file)

    def classify(self, query: str) -> Dict[str, Any]:
        if not self.enabled:
            return {
                "in_scope": True,
                "matched_topic": None,
                "keywords": [],
                "confidence": 0.0,
                "reason": "Scope model unavailable; skipping out-of-scope gate.",
            }

        X = self.vectorizer.transform([query])
        topic_probs = self.lda.transform(X)[0]
        best_topic_idx = int(topic_probs.argmax())
        best_prob = float(topic_probs[best_topic_idx])

        if best_prob < self.threshold:
            return {
                "in_scope": False,
                "matched_topic": None,
                "confidence": best_prob,
                "reason": "Query does not match any topic in the knowledge base",
            }

        topic = self._topic_at(best_topic_idx)
        return {
            "in_scope": True,
            "matched_topic": topic.get("label"),
            "keywords": topic.get("keywords", []),
            "confidence": best_prob,
            "reason": None,
        }

    def _topic_at(self, topic_idx: int) -> Dict[str, Any]:
        topics = self.manifest.get("topics", [])
        if not topics or topic_idx >= len(topics):
            return {"label": f"topic_{topic_idx}", "keywords": []}
        return topics[topic_idx]

    def _resolve_files(
        self,
        manifest_path: str,
        lda_path: str,
        vec_path: str,
        department: Optional[str],
    ) -> tuple[Optional[Path], Optional[Path], Optional[Path]]:
        dep = (department or "").strip().lower()
        if dep in {"clinical", "clinician"}:
            domain = "clinical"
        elif dep in {"manager", "management"}:
            domain = "manager"
        else:
            domain = None

        candidates: list[tuple[Path, Path, Path]] = []
        base = Path(__file__).resolve().parents[2]

        # Explicit paths first (if provided as absolute or relative in /app)
        candidates.append((Path(manifest_path), Path(lda_path), Path(vec_path)))

        # Common runtime locations inside backend container
        if domain:
            domain_dir = Path("/app/scripts/lda_domains") / domain
            candidates.append(
                (
                    domain_dir / "scope_manifest.json",
                    domain_dir / "lda_model.pkl",
                    domain_dir / "lda_vectorizer.pkl",
                )
            )
            local_domain_dir = base / "scripts" / "lda_domains" / domain
            candidates.append(
                (
                    local_domain_dir / "scope_manifest.json",
                    local_domain_dir / "lda_model.pkl",
                    local_domain_dir / "lda_vectorizer.pkl",
                )
            )

        for manifest_file, lda_file, vec_file in candidates:
            if manifest_file.exists() and lda_file.exists() and vec_file.exists():
                return manifest_file, lda_file, vec_file

        return None, None, None


async def infer_primary_knowledge_domain(query: str, margin: float = 0.06) -> Optional[Literal["clinical", "management"]]:
    """
    Decide whether the query aligns primarily with clinical vs management knowledge.
    Priority: LDA models → LLM classifier → keyword heuristic.
    Returns None only if all three methods are ambiguous.
    """
    clinical_sc = ScopeClassifier(department="clinical")
    manager_sc = ScopeClassifier(department="manager")

    # 1. LDA path — fast, no LLM cost
    if clinical_sc.enabled and manager_sc.enabled:
        cr = clinical_sc.classify(query)
        mr = manager_sc.classify(query)
        cin = bool(cr.get("in_scope"))
        min_sc = bool(mr.get("in_scope"))
        cc = float(cr.get("confidence") or 0.0)
        mc = float(mr.get("confidence") or 0.0)

        if cin and not min_sc:
            return "clinical"
        if min_sc and not cin:
            return "management"
        if cin and min_sc:
            if cc > mc + margin:
                return "clinical"
            if mc > cc + margin:
                return "management"
        # LDA ambiguous — fall through to LLM

    # 2. LLM fallback
    logger.debug("LDA models unavailable or ambiguous; using LLM fallback for domain inference.")
    llm_result = await _llm_infer_domain(query)
    if llm_result:
        return llm_result

    # 3. Keyword heuristic — always available, zero latency
    logger.debug("LLM domain inference unavailable; using keyword heuristic.")
    return _keyword_infer_domain(query)


def user_knowledge_domain_for_gap(user_role: Optional[str], user_department: Optional[str]) -> Optional[Literal["clinical", "management"]]:
    """Map account to the primary knowledge silo. None => do not enforce role/topic mismatch rules (e.g. admin)."""
    role = (user_role or "").strip().lower()
    dept = (user_department or "").strip().lower()

    if role == "admin" or dept == "admin":
        return None
    if role in {"manager"} or dept in {"management", "manager"}:
        return "management"
    if role in {"clinician"} or dept in {"clinical", "clinician"}:
        return "clinical"
    return None


def evaluate_scope_result(scope_result: Dict[str, Any], retrieved_docs: Optional[Sequence[Any]]) -> Dict[str, Any]:
    """Map scope + retrieval state to next action in the RAG flow."""
    in_scope = bool(scope_result.get("in_scope"))
    has_docs = bool(retrieved_docs)

    if not in_scope:
        return {
            "action": "out_of_scope",
            "trigger_gap_ticket": False,
            "message": (
                "This question appears outside the current knowledge base scope. "
                "Please ask a question related to indexed topics."
            ),
        }

    if has_docs:
        return {
            "action": "answer",
            "trigger_gap_ticket": False,
            "message": None,
        }

    return {
        "action": "in_scope_gap",
        "trigger_gap_ticket": True,
        "message": (
            "This question is in scope, but supporting documents were not found. "
            "A knowledge-gap ticket should be created."
        ),
    }
