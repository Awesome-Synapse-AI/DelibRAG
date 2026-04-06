from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient

from llama_index.core import StorageContext
from llama_index.core.storage.docstore import SimpleDocumentStore
from llama_index.core.vector_stores.utils import metadata_dict_to_node

# Ensure repo root on path.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

load_dotenv(ROOT / ".env", override=True)

from backend.indexing.pipeline import get_collection_persist_dir


def iter_collection_payloads(client: QdrantClient, collection: str):
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=collection,
            limit=256,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break
        for point in points:
            yield point.payload or {}
        if offset is None:
            break


def payload_to_node(payload: dict):
    if "_node_content" in payload and "_node_type" in payload:
        return metadata_dict_to_node(payload)
    metadata = payload.get("metadata")
    if isinstance(metadata, dict) and "_node_content" in metadata and "_node_type" in metadata:
        return metadata_dict_to_node(metadata)
    return None


def backfill_collection(client: QdrantClient, collection: str) -> tuple[int, int]:
    nodes = []
    skipped = 0
    for payload in iter_collection_payloads(client, collection):
        try:
            node = payload_to_node(payload)
            if node is None:
                skipped += 1
                continue
            nodes.append(node)
        except Exception:
            skipped += 1

    if not nodes:
        raise RuntimeError(
            f"No reconstructable nodes found in '{collection}'. "
            "This collection was likely not written with llama-index node metadata."
        )

    persist_dir = get_collection_persist_dir(collection)
    persist_dir.mkdir(parents=True, exist_ok=True)

    docstore = SimpleDocumentStore()
    docstore.add_documents(nodes, allow_update=True)

    # Create fresh in-memory stores, then persist to disk.
    # Using persist_dir in from_defaults() attempts to load existing files first.
    storage_context = StorageContext.from_defaults(docstore=docstore)
    storage_context.persist(persist_dir=str(persist_dir))

    return len(nodes), skipped


def main():
    parser = argparse.ArgumentParser(description="Backfill local llama-index docstore from existing Qdrant collections.")
    parser.add_argument(
        "--collections",
        nargs="+",
        default=["manager-info", "clinical-info"],
        help="Qdrant collections to backfill.",
    )
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=6333)
    args = parser.parse_args()

    client = QdrantClient(host=args.host, port=args.port, check_compatibility=False)

    for collection in args.collections:
        restored, skipped = backfill_collection(client, collection)
        print(
            f"[ok] {collection}: restored={restored}, skipped={skipped}, "
            f"persist_dir={get_collection_persist_dir(collection)}"
        )


if __name__ == "__main__":
    main()
