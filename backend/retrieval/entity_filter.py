import json
from typing import Any, List

from llama_index.llms.openai import OpenAI


ENTITY_PROMPT = """
Extract named entities from the query that should explicitly appear in evidence text.
Return strict JSON only in this format:
{{"entities": ["Entity1", "Entity2"]}}

Rules:
- Include organizations, products, person names, program names, and unique proper nouns.
- Exclude generic nouns like "metrics", "policy", "partnership", "process".
- If none exist, return {{"entities": []}}.

Query: {query}
"""


def _node_text(node: Any) -> str:
    if hasattr(node, "text"):
        return str(getattr(node, "text", ""))
    if hasattr(node, "node") and hasattr(node.node, "get_content"):
        return str(node.node.get_content())
    if isinstance(node, dict):
        return str(node.get("text", ""))
    return str(node)


def _parse_entities(raw: str) -> List[str]:
    try:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1:
            payload = json.loads(raw[start : end + 1])
            entities = payload.get("entities", [])
            if isinstance(entities, list):
                cleaned = [str(e).strip() for e in entities if str(e).strip()]
                return list(dict.fromkeys(cleaned))
    except Exception:
        pass
    return []


async def filter_nodes_by_query_entities(
    query: str,
    nodes: List[Any],
    llm_model: str = "gpt-5-nano",
) -> tuple[List[Any], List[str]]:
    if not nodes:
        return nodes, []

    llm = OpenAI(model=llm_model)
    result = await llm.acomplete(ENTITY_PROMPT.format(query=query))
    entities = _parse_entities(getattr(result, "text", str(result)))
    if not entities:
        return nodes, []

    lowered_entities = [e.lower() for e in entities]
    filtered: List[Any] = []
    for n in nodes:
        text = _node_text(n).lower()
        if all(ent in text for ent in lowered_entities):
            filtered.append(n)
    return filtered, entities
