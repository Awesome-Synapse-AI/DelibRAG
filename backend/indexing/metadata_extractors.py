from typing import List

from llama_index.core.extractors import EntityExtractor, SummaryExtractor

ALLOWED_ROLES_DEFAULT = ["viewer", "engineer", "manager", "admin"]


def infer_allowed_roles(node) -> List[str]:
    """Placeholder role inference; can be replaced with doc-level ACLs."""
    # Future: derive from document classification or embedded ACLs
    return ALLOWED_ROLES_DEFAULT


def default_metadata_extractors():
    return [
        SummaryExtractor(summaries=["prev", "self", "next"]),
        EntityExtractor(prediction_threshold=0.5),
    ]
