from typing import Iterable, List


def build_context_string(nodes: Iterable, max_chars: int = 4000) -> str:
    chunks: List[str] = []
    total = 0
    for node in nodes:
        text = _node_text(node).strip()
        if not text:
            continue
        if total + len(text) > max_chars:
            remaining = max_chars - total
            if remaining > 0:
                chunks.append(text[:remaining])
            break
        chunks.append(text)
        total += len(text)
    return "\n\n".join(chunks)


def _node_text(node) -> str:
    if isinstance(node, dict):
        return str(node.get("text", ""))
    if hasattr(node, "text"):
        return str(getattr(node, "text", ""))
    if hasattr(node, "get_text"):
        return str(node.get_text())
    if hasattr(node, "node") and hasattr(node.node, "get_content"):
        return str(node.node.get_content())
    return str(node)
