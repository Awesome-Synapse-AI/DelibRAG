import json
import logging
from pathlib import Path
from typing import Any, Dict, Optional, Sequence

import joblib

logger = logging.getLogger(__name__)


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
