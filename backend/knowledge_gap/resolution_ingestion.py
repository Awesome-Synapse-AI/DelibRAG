from pathlib import Path
from typing import Dict, List, Optional

from llama_index.core import Document
from qdrant_client import QdrantClient, models
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from indexing.pipeline import build_indexing_pipeline, build_qdrant_index
from indexing.trust_scores import (
    bump_department_trust_scores,
    mark_source_deprecated,
    update_routing_preferences,
    upsert_source_trust_score,
)
from .ticket_manager import GapTicketResolvePayload, ResolutionAction, close_ticket, get_gap_ticket


def _collection_for_department(department: Optional[str]) -> str:
    settings = get_settings()
    dep = (department or "").strip().lower()
    if dep in {"clinical", "clinician"}:
        return settings.clinical_collection_name
    if dep in {"management", "manager"}:
        return settings.manager_collection_name
    return settings.default_collection_name


def _get_qdrant_client() -> QdrantClient:
    settings = get_settings()
    return QdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
        check_compatibility=False,
    )


async def run_indexing_pipeline(
    file_path: str,
    *,
    user_role: str,
    department: Optional[str],
    extra_metadata: Optional[Dict] = None,
) -> List[Document]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"Resolution document not found: {file_path}")

    text = path.read_text(encoding="utf-8")
    metadata = {
        "doc_id": str(path),
        "source_id": str(path),
    }
    if extra_metadata:
        metadata.update(extra_metadata)
    return [Document(text=text, metadata=metadata)]


async def index_nodes(documents: List[Document], *, user_role: str, department: Optional[str]) -> None:
    client = _get_qdrant_client()
    collection_name = _collection_for_department(department)
    handlers = build_indexing_pipeline(role=user_role, department=department or "general")
    build_qdrant_index(client, collection_name, documents, handlers)


async def mark_nodes_deprecated(source_id: str, *, department: Optional[str], is_deprecated: bool = True, lock_if_false: bool = True) -> None:
    client = _get_qdrant_client()
    collection_name = _collection_for_department(department)
    source_filter = models.Filter(
        should=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="metadata.source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=source_id)),
        ],
    )

    payload: Dict[str, object] = {"is_deprecated": bool(is_deprecated)}
    if not is_deprecated and lock_if_false:
        payload["deprecation_lock"] = True

    # Best-effort: if deprecating, avoid overriding explicit lockouts.
    if is_deprecated:
        source_filter = models.Filter(
            should=source_filter.should,
            must_not=[models.FieldCondition(key="deprecation_lock", match=models.MatchValue(value=True))],
        )

    client.set_payload(collection_name=collection_name, points=source_filter, payload=payload)


async def delete_nodes_by_source(source_id: str, *, department: Optional[str]) -> None:
    client = _get_qdrant_client()
    collection_name = _collection_for_department(department)
    source_filter = models.Filter(
        should=[
            models.FieldCondition(key="source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="doc_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="metadata.source_id", match=models.MatchValue(value=source_id)),
            models.FieldCondition(key="metadata.doc_id", match=models.MatchValue(value=source_id)),
        ]
    )
    client.delete(collection_name=collection_name, points_selector=source_filter)


async def ingest_resolution(ticket_id: str, resolution: GapTicketResolvePayload, user, db: AsyncSession):
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        raise ValueError(f"Gap ticket {ticket_id} not found")

    target_department = resolution.target_department or user.department

    if resolution.action == ResolutionAction.add_document:
        if not resolution.document_path:
            raise ValueError("document_path is required for add_document resolution")
        source_id = str(Path(resolution.document_path))

        nodes = await run_indexing_pipeline(
            file_path=resolution.document_path,
            user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
            department=target_department,
            extra_metadata={
                "gap_ticket_id": ticket_id,
                "source_trust_score": 0.8,
                "resolved_by_role": user.role.value if hasattr(user.role, "value") else str(user.role),
            },
        )
        await index_nodes(
            nodes,
            user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
            department=target_department,
        )
        await upsert_source_trust_score(
            db,
            source_id=source_id,
            source_name=Path(source_id).name,
            department=target_department,
            initial_score=0.8,
        )
        await update_routing_preferences(
            db,
            department=target_department,
            prefer_source_id=source_id,
        )

    elif resolution.action == ResolutionAction.deprecate:
        raw_sources = resolution.source_ids or ([resolution.source_id] if resolution.source_id else [])
        sources = [str(s).strip() for s in raw_sources if str(s).strip()]
        if not sources:
            raise ValueError("source_id or source_ids is required for deprecate resolution")

        is_deprecated = True if resolution.is_deprecated is None else bool(resolution.is_deprecated)
        for source_id in sources:
            await mark_nodes_deprecated(source_id=source_id, department=target_department, is_deprecated=is_deprecated)
            await mark_source_deprecated(db, source_id=source_id, is_deprecated=is_deprecated)
            await update_routing_preferences(
                db,
                department=target_department,
                avoid_source_id=source_id if is_deprecated else None,
                prefer_source_id=source_id if not is_deprecated else None,
            )

    elif resolution.action == ResolutionAction.update_document:
        if not resolution.source_id or not resolution.document_path:
            raise ValueError("source_id and document_path are required for update_document resolution")
        await delete_nodes_by_source(resolution.source_id, department=target_department)
        nodes = await run_indexing_pipeline(
            resolution.document_path,
            user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
            department=target_department,
            extra_metadata={
                "gap_ticket_id": ticket_id,
                "resolved_by_role": user.role.value if hasattr(user.role, "value") else str(user.role),
            },
        )
        await index_nodes(
            nodes,
            user_role=user.role.value if hasattr(user.role, "value") else str(user.role),
            department=target_department,
        )
        new_source_id = str(Path(resolution.document_path))
        await upsert_source_trust_score(
            db,
            source_id=new_source_id,
            source_name=Path(new_source_id).name,
            department=target_department,
            initial_score=0.8,
        )
        if new_source_id != resolution.source_id:
            await update_routing_preferences(
                db,
                department=target_department,
                prefer_source_id=new_source_id,
                avoid_source_id=resolution.source_id,
            )

    await bump_department_trust_scores(db, target_department, delta=0.05)
    action_label = resolution.action.value
    notes = resolution.notes or ""
    resolution_notes = f"action={action_label}; notes={notes}".strip()
    return await close_ticket(
        db,
        ticket_id=ticket_id,
        resolver_id=str(user.id),
        resolution_notes=resolution_notes,
    )
