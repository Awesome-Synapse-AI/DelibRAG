import json
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import joblib


class ScopeClassifier:
    def __init__(
        self,
        manifest_path: str = "scope_manifest.json",
        lda_path: str = "lda_model.pkl",
        vec_path: str = "lda_vectorizer.pkl",
        threshold: float = 0.15,
    ):
        manifest_file = Path(manifest_path)
        lda_file = Path(lda_path)
        vec_file = Path(vec_path)

        if not manifest_file.exists():
            raise FileNotFoundError(f"Scope manifest not found: {manifest_file}")
        if not lda_file.exists():
            raise FileNotFoundError(f"LDA model file not found: {lda_file}")
        if not vec_file.exists():
            raise FileNotFoundError(f"LDA vectorizer file not found: {vec_file}")

        with manifest_file.open("r", encoding="utf-8") as f:
            self.manifest: Dict[str, Any] = json.load(f)

        self.lda = joblib.load(lda_file)
        self.vectorizer = joblib.load(vec_file)
        self.threshold = threshold

    def classify(self, query: str) -> Dict[str, Any]:
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
