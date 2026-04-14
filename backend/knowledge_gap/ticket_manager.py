import enum
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field
from sqlalchemy import Column, DateTime, ForeignKey, String, Text, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from db.postgres import Base


class GapType(str, enum.Enum):
    missing_knowledge = "missing_knowledge"
    contradiction = "contradiction"
    low_confidence = "low_confidence"


class TicketStatus(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    wont_fix = "wont_fix"


class ResolutionAction(str, enum.Enum):
    add_document = "add_document"
    deprecate = "deprecate"
    update_document = "update_document"


class GapTicket(Base):
    __tablename__ = "gap_tickets"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    query = Column(Text, nullable=False)
    created_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    department = Column(String(100), nullable=True)
    gap_type = Column(String(50), nullable=False, default=GapType.missing_knowledge.value)
    description = Column(Text, nullable=False)
    conflicting_sources = Column(ARRAY(Text), nullable=True)
    suggested_owner = Column(String(255), nullable=True)
    assigned_to_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    status = Column(String(20), nullable=False, default=TicketStatus.open.value)
    resolution_notes = Column(Text, nullable=True)
    resolved_by_user_id = Column(UUID(as_uuid=True), ForeignKey("users.id"), nullable=True)
    resolved_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())


class GapTicketResolvePayload(BaseModel):
    action: ResolutionAction
    document_path: Optional[str] = None
    source_id: Optional[str] = None
    source_ids: Optional[list[str]] = None
    is_deprecated: Optional[bool] = None
    target_department: Optional[str] = None
    notes: Optional[str] = None


class GapTicketAssignPayload(BaseModel):
    assignee_user_id: str = Field(..., description="UUID of the assignee")


def _maybe_uuid(value: Optional[str]):
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


async def create_gap_ticket(db: AsyncSession, payload: Dict[str, Any]) -> GapTicket:
    query = str(payload.get("query") or "").strip()
    if not query:
        raise ValueError("query is required")

    gap_type = payload.get("gap_type", GapType.missing_knowledge.value)
    ticket = GapTicket(
        query=query,
        description=payload.get("description", ""),
        gap_type=gap_type,
        status=payload.get("status", TicketStatus.open.value),
        created_by_user_id=_maybe_uuid(payload.get("user_id")) if payload.get("user_id") else None,
        department=payload.get("department"),
        suggested_owner=payload.get("suggested_owner"),
        conflicting_sources=payload.get("conflicting_sources"),
    )
    db.add(ticket)
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def list_gap_tickets(db: AsyncSession, status: Optional[str] = TicketStatus.open.value) -> List[GapTicket]:
    stmt = select(GapTicket).order_by(GapTicket.created_at.desc())
    if status:
        stmt = stmt.where(GapTicket.status == status)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def get_gap_ticket(db: AsyncSession, ticket_id: str) -> Optional[GapTicket]:
    result = await db.execute(select(GapTicket).where(GapTicket.id == _maybe_uuid(ticket_id)))
    return result.scalars().first()


async def assign_gap_ticket(db: AsyncSession, ticket_id: str, assignee_user_id: str) -> Optional[GapTicket]:
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        return None
    ticket.assigned_to_user_id = _maybe_uuid(assignee_user_id)
    ticket.status = TicketStatus.in_progress.value
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def close_ticket(
    db: AsyncSession,
    ticket_id: str,
    resolver_id: str,
    resolution_notes: Optional[str] = None,
) -> Optional[GapTicket]:
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        return None
    ticket.status = TicketStatus.resolved.value
    ticket.resolved_by_user_id = _maybe_uuid(resolver_id)
    ticket.resolved_at = datetime.utcnow()
    ticket.resolution_notes = resolution_notes
    await db.commit()
    await db.refresh(ticket)
    return ticket


async def delete_gap_ticket(db: AsyncSession, ticket_id: str) -> bool:
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        return False
    await db.delete(ticket)
    await db.commit()
    return True
