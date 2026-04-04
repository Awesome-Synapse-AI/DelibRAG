from typing import Any, Dict

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from auth.dependencies import require_role
from auth.models import UserRole
from db.postgres import get_db
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
    _user=Depends(require_role(UserRole.engineer, UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    tickets = await list_gap_tickets(db, status=status_filter)
    return [_serialize_ticket(t) for t in tickets]


@router.get("/{ticket_id}")
async def get_gap(
    ticket_id: str,
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.engineer, UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    ticket = await get_gap_ticket(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
    return _serialize_ticket(ticket)


@router.post("")
async def create_gap(
    payload: Dict[str, Any],
    db: AsyncSession = Depends(get_db),
    user=Depends(require_role(UserRole.engineer, UserRole.clinician, UserRole.manager, UserRole.admin)),
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
    user=Depends(require_role(UserRole.engineer, UserRole.clinician, UserRole.manager, UserRole.admin)),
):
    try:
        ticket = await ingest_resolution(ticket_id=ticket_id, resolution=payload, user=user, db=db)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Gap ticket not found")
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
