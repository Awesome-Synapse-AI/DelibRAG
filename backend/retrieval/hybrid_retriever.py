import re
from typing import List

from llama_index.core.retrievers import AutoMergingRetriever, BaseRetriever, QueryFusionRetriever
from llama_index.core.schema import NodeWithScore, QueryBundle
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters


class LexicalGateFusionRetriever(BaseRetriever):
    def __init__(self, fusion_retriever: QueryFusionRetriever):
        super().__init__()
        self._fusion_retriever = fusion_retriever

    def _retrieve(self, query_bundle: QueryBundle) -> List[NodeWithScore]:
        candidates = self._fusion_retriever.retrieve(query_bundle)
        query_text = query_bundle.query_str or ""
        query_terms = self._keywords(query_text)
        
        if not query_terms:
            return candidates

        min_hits = 2 if len(query_terms) >= 4 else 1
        filtered: List[NodeWithScore] = []
        for node in candidates:
            node_text = self._node_text(node)

            node_terms = self._keywords(node_text)
            if len(query_terms & node_terms) >= min_hits:
                filtered.append(node)
        return filtered

    @staticmethod
    def _node_text(node: NodeWithScore) -> str:
        if hasattr(node, "text"):
            return str(getattr(node, "text", ""))
        if hasattr(node, "node") and hasattr(node.node, "get_content"):
            return str(node.node.get_content())
        return str(node)

    @staticmethod
    def _keywords(text: str) -> set[str]:
        tokens = re.findall(r"[a-zA-Z][a-zA-Z0-9_-]+", text.lower())
        stopwords = {
            "the",
            "and",
            "for",
            "with",
            "from",
            "that",
            "this",
            "about",
            "tell",
            "what",
            "when",
            "where",
            "who",
            "why",
            "how",
            "your",
            "have",
            "has",
            "had",
            "are",
            "was",
            "were",
            "will",
            "would",
            "should",
            "can",
            "could",
            "not",
            "any",
            "all",
            "amount",
            "criteria",
        }
        return {tok for tok in tokens if len(tok) >= 3 and tok not in stopwords}


    @staticmethod
    def _contains_all(text: str, required_terms: set[str]) -> bool:
        lowered = text.lower()
        return all(term in lowered for term in required_terms)


def build_hybrid_retriever(index, storage_context, user):
    vector_retriever = build_vector_retriever(index=index, user=user, similarity_top_k=20)

    docstore = storage_context.docstore
    docs = getattr(docstore, "docs", {}) if docstore is not None else {}
    if not docs:
        raise ValueError(
            "BM25 requires a non-empty local docstore. Rebuild index with "
            "scripts/run_llamaindex.py so docstore + hierarchy are persisted."
        )

    bm25 = BM25Retriever.from_defaults(
        docstore=docstore,
        similarity_top_k=20,
    )

    fusion_retriever = QueryFusionRetriever(
        retrievers=[bm25, vector_retriever],
        similarity_top_k=20,
        num_queries=1,
        mode="relative_score",
        retriever_weights=[0.3, 0.7],
        use_async=False,
    )
    gated_fusion_retriever = LexicalGateFusionRetriever(fusion_retriever)

    auto_merging = AutoMergingRetriever(
        gated_fusion_retriever,
        storage_context,
        simple_ratio_thresh=0.4,
    )

    return auto_merging


def build_vector_retriever(index, user, similarity_top_k: int = 20):
    return index.as_retriever(
        similarity_top_k=similarity_top_k,
        filters=MetadataFilters(
            filters=[
                MetadataFilter(key="allowed_roles", value=user.role),
                MetadataFilter(key="department", value=user.department),
            ]
        ),
    )
