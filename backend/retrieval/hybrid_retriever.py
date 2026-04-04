from llama_index.core.retrievers import AutoMergingRetriever, QueryFusionRetriever
from llama_index.retrievers.bm25 import BM25Retriever
from llama_index.core.vector_stores.types import MetadataFilter, MetadataFilters


def build_hybrid_retriever(index, storage_context, user):
    bm25 = BM25Retriever.from_defaults(
        docstore=storage_context.docstore,
        similarity_top_k=10,
    )

    vector_retriever = index.as_retriever(
        similarity_top_k=10,
        filters=MetadataFilters(
            filters=[
                MetadataFilter(key="allowed_roles", value=user.role),
                MetadataFilter(key="department", value=user.department),
                MetadataFilter(key="is_deprecated", value=False),
            ]
        ),
    )

    fusion_retriever = QueryFusionRetriever(
        retrievers=[bm25, vector_retriever],
        similarity_top_k=5,
        num_queries=1,
        mode="reciprocal_rerank",
    )

    auto_merging = AutoMergingRetriever(
        fusion_retriever,
        storage_context,
        simple_ratio_thresh=0.4,
    )

    return auto_merging
