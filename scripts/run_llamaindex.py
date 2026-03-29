from pathlib import Path
import sys
import os

from qdrant_client import QdrantClient
from llama_index.core import Document
from dotenv import load_dotenv

# Ensure repo root is on sys.path when running as a script
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Load environment variables from repo-level .env
# override=True ensures stale shell values do not shadow .env entries.
load_dotenv(ROOT / ".env", override=True)

from backend.indexing.pipeline import build_indexing_pipeline, build_qdrant_index


COLLECTIONS = [
    {
        "name": "manager-info",
        "files": [
            # str(ROOT / "sample-docs" / "low-stake-manager-doc.md"),
            str(ROOT / "sample-docs" / "high-stake-manager-doc.md"),
        ],
        "role": "manager",
        "department": "management",
    },
    {
        "name": "clinical-info",
        "files": [
            # str(ROOT / "sample-docs" / "low-stake-clinical-doc.md"),
            str(ROOT / "sample-docs" / "high-stake-clinical-doc.md"),
        ],
        "role": "clinician",
        "department": "clinical",
    },
]


def load_documents(file_paths: list[str]) -> list[Document]:
    docs: list[Document] = []
    for path in file_paths:
        p = Path(path)
        if not p.exists():
            print(f"Skipping missing file: {p}")
            continue
        text = p.read_text(encoding="utf-8")
        docs.append(Document(text=text, metadata={"doc_id": str(p)}))
    return docs


def main():
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("Missing OPENAI_API_KEY. Add it to .env at repo root or export it in your shell.")

    client = QdrantClient(host="localhost", port=6333, check_compatibility=False)

    for cfg in COLLECTIONS:
        docs = load_documents(cfg["files"])
        if not docs:
            print(f"No docs for collection {cfg['name']}; skipping.")
            continue

        handlers = build_indexing_pipeline(cfg["role"], cfg["department"])
        build_qdrant_index(client, cfg["name"], docs, handlers)
        print(f"Indexed {len(docs)} docs into collection {cfg['name']}")


if __name__ == "__main__":
    main()
