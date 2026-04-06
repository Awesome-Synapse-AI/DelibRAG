import csv
import io

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse

from auth.dependencies import require_role
from auth.models import UserRole
from .trail import export_all_audits, get_audit_for_query, get_audit_for_session


router = APIRouter()


@router.get("/session/{session_id}")
async def audit_session(session_id: str, _user=Depends(require_role(UserRole.manager, UserRole.admin))):
    return await get_audit_for_session(session_id)


@router.get("/query/{query_id}")
async def audit_query(query_id: str, _user=Depends(require_role(UserRole.manager, UserRole.admin))):
    doc = await get_audit_for_query(query_id)
    if not doc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Audit entry not found")
    return doc


@router.get("/export")
async def audit_export(_user=Depends(require_role(UserRole.admin))):
    rows = await export_all_audits()
    output = io.StringIO()
    fieldnames = [
        "session_id",
        "query_id",
        "timestamp",
        "user_id",
        "user_role",
        "stakes_level",
        "query_complexity",
        "consequence_severity",
        "confidence",
        "confidence_gate_passed",
        "requires_human_review",
        "final_answer",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        stakes = row.get("stakes_classification") or {}
        writer.writerow(
            {
                "session_id": row.get("session_id"),
                "query_id": row.get("query_id"),
                "timestamp": row.get("timestamp"),
                "user_id": row.get("user_id"),
                "user_role": row.get("user_role"),
                "stakes_level": stakes.get("stakes_level"),
                "query_complexity": stakes.get("query_complexity"),
                "consequence_severity": stakes.get("consequence_severity"),
                "confidence": row.get("confidence"),
                "confidence_gate_passed": row.get("confidence_gate_passed"),
                "requires_human_review": row.get("requires_human_review"),
                "final_answer": row.get("final_answer"),
            }
        )
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=audit_export.csv"},
    )
