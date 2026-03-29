from typing import List, Optional

# Summary extractor import (path varies by version)
try:
    from llama_index.extractors.summary import SummaryExtractor  # >=0.10.40+
except ImportError:  # pragma: no cover
    from llama_index.core.extractors import SummaryExtractor  # older versions

from llama_index.core.extractors.metadata_extractors import KeywordExtractor

ALLOWED_ROLES_DEFAULT = ["viewer", "clinician", "manager", "admin"]
ALLOWED_ROLES_CLINICAL = ["viewer", "clinician", "admin"]
ALLOWED_ROLES_MANAGER = ["viewer", "manager", "admin"]


def infer_allowed_roles(node, department: Optional[str] = None) -> List[str]:
    """Infer ACL roles from department first, then doc_id as a fallback."""
    normalized_department = (department or "").strip().lower()
    if normalized_department in {"clinical", "clinician"}:
        return ALLOWED_ROLES_CLINICAL
    if normalized_department in {"management", "manager"}:
        return ALLOWED_ROLES_MANAGER

    doc_id = str(node.metadata.get("doc_id", "")).lower()
    if "clinical" in doc_id:
        return ALLOWED_ROLES_CLINICAL
    if "manager" in doc_id or "management" in doc_id:
        return ALLOWED_ROLES_MANAGER

    return ALLOWED_ROLES_DEFAULT


def default_metadata_extractors():
    # Use LLM-based keyword extractor to approximate entities without torch/span-marker.
    return [
        SummaryExtractor(summaries=["prev", "self", "next"]),
        KeywordExtractor(keywords=10),
    ]
