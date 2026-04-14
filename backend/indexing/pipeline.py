from pathlib import Path
from typing import Tuple

from llama_index.core.node_parser import HierarchicalNodeParser, SentenceSplitter
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import Document, StorageContext, VectorStoreIndex, Settings
from llama_index.core.ingestion import run_transformations
from llama_index.embeddings.openai import OpenAIEmbedding
from llama_index.llms.openai import OpenAI

from .metadata_extractors import default_metadata_extractors, infer_allowed_roles


def build_indexing_pipeline(
    role: str,
    department: str,
    llm_model: str = "gpt-5-nano",
    embedding_model: str = "text-embedding-3-small",
):
    # Ensure global Settings are configured before extractors instantiate
    Settings.llm = OpenAI(model=llm_model)
    Settings.embed_model = OpenAIEmbedding(model=embedding_model)

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
            node.metadata["department"] = department
            node.metadata["allowed_roles"] = infer_allowed_roles(node, department=department)
            node.metadata["uploaded_by_role"] = role
            node.metadata["source_trust_score"] = 1.0
            node.metadata["is_deprecated"] = False
            node.metadata["deprecation_lock"] = False
        return nodes

    return node_parser, metadata_extractors, inject_custom_metadata


def build_qdrant_index(
    client,
    collection: str,
    docs: list[Document],
    metadata_handlers: Tuple,
    embedding_model: str = "text-embedding-3-small",
    llm_model: str = "gpt-5-nano",
) -> VectorStoreIndex:
    node_parser, metadata_extractors, metadata_injector = metadata_handlers

    previous_embed_model = Settings.embed_model
    previous_llm = Settings.llm
    Settings.embed_model = OpenAIEmbedding(model=embedding_model)
    Settings.llm = OpenAI(model=llm_model)

    vector_store = QdrantVectorStore(client=client, collection_name=collection)
    persist_dir = get_collection_persist_dir(collection)
    persist_dir.mkdir(parents=True, exist_ok=True)
    storage_context = StorageContext.from_defaults(
        vector_store=vector_store,
        persist_dir=str(persist_dir),
    )

    try:
        transformed_nodes = run_transformations(
            docs,
            transformations=[node_parser, *metadata_extractors],
            show_progress=True,
        )
        transformed_nodes = metadata_injector(transformed_nodes)

        index = VectorStoreIndex(
            transformed_nodes,
            storage_context=storage_context,
            # Force keeping full node graph in docstore so BM25/AutoMerging can run at query time.
            store_nodes_override=True,
            show_progress=True,
        )
        storage_context.persist(persist_dir=str(persist_dir))
    finally:
        Settings.embed_model = previous_embed_model
        Settings.llm = previous_llm
    return index


def get_collection_persist_dir(collection: str) -> Path:
    backend_root = Path(__file__).resolve().parents[1]
    return backend_root / ".index_store" / collection
