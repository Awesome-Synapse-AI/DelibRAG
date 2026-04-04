from fastapi import APIRouter, Depends, HTTPException, status

from auth.dependencies import require_role
from auth.models import UserRole


router = APIRouter()


@router.get("/session/{session_id}")
async def audit_session(session_id: str, _user=Depends(require_role(UserRole.manager, UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Audit session endpoint not implemented yet")


@router.get("/query/{query_id}")
async def audit_query(query_id: str, _user=Depends(require_role(UserRole.manager, UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Audit query endpoint not implemented yet")


@router.get("/export")
async def audit_export(_user=Depends(require_role(UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Audit export not implemented yet")
