from fastapi import APIRouter, Depends, HTTPException, status

from auth.dependencies import require_role
from auth.models import UserRole


router = APIRouter()


@router.post("/index")
async def index_document(_user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Indexing endpoint not implemented yet")


@router.post("/reindex")
async def reindex_corpus(_user=Depends(require_role(UserRole.manager, UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Reindex endpoint not implemented yet")


@router.get("/sources")
async def list_sources(_user=Depends(require_role(UserRole.clinician, UserRole.manager, UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Sources listing not implemented yet")


@router.patch("/trust/{source_id}")
async def update_trust_score(source_id: str, _user=Depends(require_role(UserRole.admin))):
    raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "Trust score override not implemented yet")
