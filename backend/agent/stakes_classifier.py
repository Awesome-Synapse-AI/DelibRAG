from typing import Optional


def classify_stakes(query: str, user_role: Optional[str] = None) -> str:
    q = (query or "").lower()
    if any(k in q for k in ["diagnosis", "treatment", "medication", "surgery"]):
        return "high"
    if any(k in q for k in ["policy", "budget", "compliance"]):
        return "medium"
    if user_role == "admin":
        return "medium"
    return "low"
