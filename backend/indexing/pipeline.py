from typing import Callable, Tuple

from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext, VectorStoreIndex

from .metadata_extractors import default_metadata_extractors, infer_allowed_roles


def build_indexing_pipeline(role: str, department: str):
    # 1. Hierarchical parser with sentence-level chunking
    node_parser = HierarchicalNodeParser.from_defaults(
        node_parser_ids=["large", "medium", "small"],
        node_parser_map={
            "large": SentenceSplitter(chunk_size=2048, chunk_overlap=64),
            "medium": SentenceSplitter(chunk_size=512, chunk_overlap=32),
            "small": SentenceSplitter(chunk_size=128, chunk_overlap=16),
        },
    )

    # 2. Metadata extractors
    metadata_extractors = default_metadata_extractors()

    # 3. Custom metadata injector (role/department access control)
    def inject_custom_metadata(nodes):
        for node in nodes:
            node.metadata["allowed_roles"] = infer_allowed_roles(node)
            node.metadata["department"] = department
            node.metadata["uploaded_by_role"] = role
            node.metadata["source_trust_score"] = 1.0
            node.metadata["is_deprecated"] = False
        return nodes

    return node_parser, metadata_extractors, inject_custom_metadata


def build_qdrant_index(client, collection: str, nodes, metadata_handlers: Tuple) -> VectorStoreIndex:
    node_parser, metadata_extractors, metadata_injector = metadata_handlers

    parsed_nodes = node_parser.get_nodes_from_documents(nodes)
    parsed_nodes = metadata_injector(parsed_nodes)

    vector_store = QdrantVectorStore(client=client, collection_name=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store)

    index = VectorStoreIndex.from_documents(
        parsed_nodes,
        storage_context=storage_context,
        transformations=metadata_extractors,
        show_progress=True,
    )
    return index
