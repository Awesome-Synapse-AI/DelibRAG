import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from qdrant_client import AsyncQdrantClient, QdrantClient
from llama_index.vector_stores.qdrant import QdrantVectorStore
from llama_index.core import StorageContext, VectorStoreIndex

from auth.dependencies import require_role
from auth.models import UserRole
from config import get_settings
from agent.nodes import drop_deprecated_nodes
from db.postgres import get_db
from indexing.pipeline import get_collection_persist_dir
from retrieval.hybrid_retriever import build_hybrid_retriever, build_vector_retriever
from retrieval.entity_filter import filter_nodes_by_query_entities
from retrieval.scope_classifier import ScopeClassifier
from .detector import GapDetector
from .resolution_ingestion import ingest_resolution
from .ticket_manager import (
    GapTicket,
    GapTicketAssignPayload,
    GapTicketResolvePayload,
    assign_gap_ticket,
    create_gap_ticket,
    delete_gap_ticket,
    get_gap_ticket,
    list_gap_tickets,
)


router = APIRouter()
settings = get_settings()


class GapDetectorCheckRequest(BaseModel):
    role: UserRole
    query: str
    department: str | None = None
    confidence: float | None = None
    force_in_scope: bool | None = None


class AddDocumentTextPayload(BaseModel):
    filename: str
    text: str
    target_department: Optional[str] = None
    notes: Optional[str] = None


class DeprecateSourcesPayload(BaseModel):
    source_ids: List[str]
    is_deprecated: bool = True
    target_department: Optional[str] = None
    notes: Optional[str] = None


def _parse_semver(version: str) -> tuple[int, int, int]:
    cleaned = version.split("-")[0]
    parts = cleaned.split(".")
    nums = []
    for p in parts[:3]:
        try:
            nums.append(int(p))
        except ValueError:
            nums.append(0)
    while len(nums) < 3:
        nums.append(0)
    return tuple(nums)


def _ensure_qdrant_ready(client: QdrantClient, collection: str):
    try:
        if not client.collection_exists(collection):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Qdrant collection '{collection}' not found. Run indexing first.",
            )
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Cannot reach Qdrant or validate collection '{collection}': {exc}",
        )

    try:
        info = client.info()
        version = getattr(info, "version", None)
        if version is None and isinstance(info, dict):
            version = info.get("version")
        if version and _parse_semver(str(version)) < (1, 10, 0):
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail=(
                    "Qdrant server version is too old for this backend. "
                    "Use Qdrant >= 1.10 (recommended 1.17.x) or downgrade qdrant-client/llama-index packages."
                ),
            )
    except HTTPException:
        raise
    except Exception:
        pass


def _collection_for_department(department: str) -> str:
    dep = (department or "").strip().lower()
    if dep in {"clinical", "clinician"}:
        return settings.clinical_collection_name
    if dep in {"management", "manager"}:
        return settings.manager_collection_name
    return settings.default_collection_name


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _sample_docs_dir() -> Path:
    configured = (os.getenv("SAMPLE_DOCS_DIR") or "").strip()
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured).expanduser())

    repo_root = _repo_root()
    candidates.extend(
        [
            repo_root / "sample-docs",
            repo_root.parent / "sample-docs",
            Path.cwd() / "sample-docs",
            Path("/sample-docs"),
        ]
    )

    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.exists() and resolved.is_dir():
            return resolved

    # Fall back to the first candidate path and let callers create it.
    return candidates[0].resolve()


_FILENAME_RE = re.compile(r"^[a-zA-Z0-9._-]+$")


def _sanitize_filename(filename: str) -> str:
    name = (filename or "").strip()
    name = Path(name).name
    if not name or not _FILENAME_RE.match(name):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid filename")
    if not name.lower().endswith((".txt", ".md")):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Only .txt and .md files are allowed")
    return name


def _normalize_source_id(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return value

    # If user provides a bare filename or a relative sample-docs path,
    # normalize to the absolute path we store in Qdrant metadata.
    docs_dir = _sample_docs_dir()
    lowered = value.lower().replace("\\", "/")
    if lowered.startswith("sample-docs/"):
        name = _sanitize_filename(lowered.split("/", 1)[1])
        return str(docs_dir / name)
    if "/" not in lowered and "\\" not in value and ":" not in value:
        if lowered.endswith((".txt", ".md")):
            name = _sanitize_filename(value)
            return str(docs_dir / name)
    return value


def _allowed_departments_for_user(user) -> list[str]:
    role_value = getattr(user, "role", "")
    role = role_value.value if hasattr(role_value, "value") else str(role_value)
    normalized = (role or "").strip().lower()
    if normalized == UserRole.admin.value:
        return ["clinical", "management", "general"]
    if normalized == UserRole.manager.value:
        return ["management", "general"]
    return ["clinical", "general"]


def _validate_target_department(user, target_department: Optional[str]) -> str:
    dep = (target_department or getattr(user, "department", None) or "general").strip().lower()
    if dep in {"manager"}:
        dep = "management"
    if dep in {"clinician"}:
        dep = "clinical"
    if dep not in {"clinical", "management", "general"}:
        dep = "general"
    if dep not in _allowed_departments_for_user(user):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not allowed to index into selected department")
    return dep


def _serialize_ticket(ticket: GapTicket) -> Dict[str, Any]:
    return {
        "id": str(ticket.id),
        "query": ticket.query,
        "description": ticket.description,
        "gap_type": ticket.gap_type,
        "status": ticket.status,
        "created_by_user_id": str(ticket.created_by_user_id) if ticket.created_by_user_id else None,
        "department": ticket.department,
        "suggested_owner": ticket.suggested_owner,
        "conflicting_sources": ticket.conflicting_sources,
        "assigned_to_user_id": str(ticket.assigned_to_user_id) if ticket.assigned_to_user_id else None,
        "resolution_notes": ticket.resolution_notes,
        "resolved_by_user_id": str(ticket.resolved_by_user_id) if ticket.resolved_by_user_id else None,
        "resolved_at": ticket.resolved_at.isoformat() if ticket.resolved_at else None,
        "created_at": ticket.created_at.isoformat() if ticket.created_at else None,
    }


@router.get("")
async def get_gaps(
    status_filter: str = Query(default="open", alias="status"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    tickets = await list_gap_tickets(db, status=status_filter)
    return [_serialize_ticket(t) for t in tickets]


@router.post("/check/detector/dev")
async def check_gap_detector(
    payload: GapDetectorCheckRequest,
):
    if payload.department:
        department = payload.department
    elif payload.role == UserRole.clinician:
        department = "clinical"
    elif payload.role == UserRole.manager:
        department = "management"
    else:
        department = "general"

    collection = _collection_for_department(department)
    persist_dir = get_collection_persist_dir(collection)
    if not persist_dir.exists():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Missing local docstore for '{collection}'. "
                "Run indexing (scripts/run_llamaindex.py or backfill script) before detector checks."
            ),
        )

    client = QdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
    _ensure_qdrant_ready(client, collection)
    aclient = AsyncQdrantClient(host=settings.qdrant_host, port=settings.qdrant_port, check_compatibility=False)
    vector_store = QdrantVectorStore(client=client, aclient=aclient, collection_name=collection)
    storage_context = StorageContext.from_defaults(vector_store=vector_store, persist_dir=str(persist_dir))
    index = VectorStoreIndex.from_vector_store(vector_store, storage_context=storage_context)

    simulated_user = type("User", (), {"role": payload.role.value, "department": department})
    vector_retriever = build_vector_retriever(index=index, user=simulated_user, similarity_top_k=10)
    raw_vector_nodes = drop_deprecated_nodes(await vector_retriever.aretrieve(payload.query))
    raw_vector_max_score = max((float(getattr(n, "score", 0.0) or 0.0) for n in raw_vector_nodes), default=0.0)

    retriever = build_hybrid_retriever(index=index, storage_context=storage_context, user=simulated_user)
    retrieved_nodes = drop_deprecated_nodes(await retriever.aretrieve(payload.query))
    retrieved_nodes, query_entities = await filter_nodes_by_query_entities(payload.query, retrieved_nodes)

    scope_result = ScopeClassifier(department=department).classify(payload.query)
    in_scope = payload.force_in_scope if payload.force_in_scope is not None else bool(scope_result.get("in_scope"))

    state = {
        "query": payload.query,
        "user_id": "dev-check-user",
        "user_role": payload.role.value,
        "user_department": department,
        "retrieved_nodes": retrieved_nodes,
        "scope_result": scope_result,
        "in_scope": in_scope,
        "confidence": payload.confidence if payload.confidence is not None else 1.0,
        "raw_vector_max_score": raw_vector_max_score,
        "query_entities": query_entities,
    }

    detector = GapDetector()
    ticket_preview = await detector.check_gap(state)
    max_score = max((detector._node_score(n) for n in retrieved_nodes), default=0.0)
    query_terms = detector._query_keywords(payload.query)
    best_coverage = max((detector._keyword_coverage(query_terms, detector._node_text(n)) for n in retrieved_nodes), default=0.0)
    top_nodes_debug = []
    for idx, node in enumerate(retrieved_nodes[:5], start=1):
        text = detector._node_text(node).strip()
        top_nodes_debug.append(
            {
                "rank": idx,
                "score": detector._node_score(node),
                "source": detector._node_source(node),
                "text_preview": text[:500],
            }
        )

    return {
        "triggered": ticket_preview is not None,
        "collection": collection,
        "scope_result": scope_result,
        "retrieval": {
            "count": len(retrieved_nodes),
            "max_score": max_score,
            "raw_vector_max_score": raw_vector_max_score,
            "best_keyword_coverage": best_coverage,
            "query_entities": query_entities,
            "top_sources": [detector._node_source(n) for n in retrieved_nodes[:5]],
            "top_nodes": top_nodes_debug,
        },
        "ticket_preview": ticket_preview,
    }


@router.get("/{ticket_id}")
async def get_gap(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    return _serialize_ticket(ticket)


@router.post("")
async def create_gap(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    payload.setdefault("user_id", str(user.id))
    payload.setdefault("user_role", user.role.value if hasattr(user.role, "value") else str(user.role))
    payload.setdefault("department", user.department)
    payload.setdefault("status", "open")
    try:
        ticket = await create_gap_ticket(db, payload)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_ticket(ticket)


@router.post("/{ticket_id}/assign")
async def assign_gap(
    ticket_id: str,
    payload: GapTicketAssignPayload,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.manager, UserRole.admin)),
):
    ticket = await assign_gap_ticket(db, ticket_id, payload.assignee_user_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    return _serialize_ticket(ticket)


@router.post("/{ticket_id}/resolve")
async def resolve_gap(
    ticket_id: str,
    payload: GapTicketResolvePayload,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    if payload.target_department is not None:
        payload.target_department = _validate_target_department(user, payload.target_department)
    if payload.action == "deprecate":
        if payload.source_id:
            payload.source_id = _normalize_source_id(payload.source_id)
        if payload.source_ids:
            payload.source_ids = [_normalize_source_id(s) for s in payload.source_ids if str(s).strip()]
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=payload, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    return _serialize_ticket(ticket)


@router.get("/docs/list")
async def list_sample_documents(
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    _ = db  # reserved for future auditing
    docs_dir = _sample_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    entries = []
    for p in sorted(docs_dir.iterdir()):
        if not p.is_file():
            continue
        if not p.name.lower().endswith((".txt", ".md")):
            continue
        stat = p.stat()
        entries.append({"name": p.name, "size": stat.st_size, "modified_at": stat.st_mtime})
    return {"documents": entries}


@router.post("/{ticket_id}/resolve/add_document/upload")
async def resolve_add_document_upload(
    ticket_id: str,
    file: UploadFile = File(...),
    filename: str | None = Form(default=None),
    target_department: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    dep = _validate_target_department(user, target_department)
    final_name = _sanitize_filename(filename or file.filename or "")
    docs_dir = _sample_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / final_name
    content = await file.read()
    path.write_bytes(content)

    payload = GapTicketResolvePayload(
        action="add_document",
        document_path=str(path),
        notes=notes,
        target_department=dep,
    )
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=payload, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_ticket(ticket)


@router.post("/{ticket_id}/resolve/add_document/text")
async def resolve_add_document_text(
    ticket_id: str,
    payload: AddDocumentTextPayload,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    dep = _validate_target_department(user, payload.target_department)
    final_name = _sanitize_filename(payload.filename)
    docs_dir = _sample_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / final_name
    path.write_text(payload.text or "", encoding="utf-8")

    resolution = GapTicketResolvePayload(
        action="add_document",
        document_path=str(path),
        notes=payload.notes,
        target_department=dep,
    )
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=resolution, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_ticket(ticket)


@router.post("/{ticket_id}/resolve/update_document/upload")
async def resolve_update_document_upload(
    ticket_id: str,
    target_filename: str = Form(...),
    file: UploadFile = File(...),
    target_department: str | None = Form(default=None),
    notes: str | None = Form(default=None),
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    dep = _validate_target_department(user, target_department)
    final_name = _sanitize_filename(target_filename)
    docs_dir = _sample_docs_dir()
    docs_dir.mkdir(parents=True, exist_ok=True)
    path = docs_dir / final_name
    content = await file.read()
    path.write_bytes(content)

    resolution = GapTicketResolvePayload(
        action="update_document",
        source_id=str(path),
        document_path=str(path),
        notes=notes,
        target_department=dep,
    )
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=resolution, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_ticket(ticket)


@router.post("/{ticket_id}/resolve/deprecate")
async def resolve_deprecate_sources(
    ticket_id: str,
    payload: DeprecateSourcesPayload,
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    dep = _validate_target_department(user, payload.target_department)
    normalized_sources = [_normalize_source_id(s) for s in (payload.source_ids or []) if str(s).strip()]
    resolution = GapTicketResolvePayload(
        action="deprecate",
        source_ids=normalized_sources,
        is_deprecated=payload.is_deprecated,
        notes=payload.notes,
        target_department=dep,
    )
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=resolution, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    return _serialize_ticket(ticket)


class UpdateStatusPayload(BaseModel):
    status: str


@router.patch("/{ticket_id}/status")
async def update_gap_status(
    ticket_id: str,
    payload: UpdateStatusPayload,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.manager, UserRole.admin)),
):
    from .ticket_manager import TicketStatus
    
    # Validate status
    try:
        TicketStatus(payload.status)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid status: {payload.status}. Must be one of: open, in_progress, resolved, wont_fix"
        )
    
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    
    ticket.status = payload.status
    await db.commit()
    await db.refresh(ticket)
    return _serialize_ticket(ticket)


@router.delete("/{ticket_id}")
async def delete_gap(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.admin)),
):
    deleted = await delete_gap_ticket(db, ticket_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    return {"deleted": True, "id": ticket_id}
