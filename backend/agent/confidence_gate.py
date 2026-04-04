def confidence_gate(confidence: float, minimum: float = 0.45) -> bool:
    return float(confidence) >= float(minimum)
