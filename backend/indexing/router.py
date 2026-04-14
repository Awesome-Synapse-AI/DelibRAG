from fastapi import APIRouter, Depends, HTTPException, status

from auth.dependencies import get_current_user, require_role
from auth.models import UserRole
from config import get_settings


router = APIRouter()
settings = get_settings()


def _effective_role(user) -> str:
    role_value = getattr(user, "role", "")
    return role_value.value if hasattr(role_value, "value") else str(role_value)


def _allowed_departments(user) -> list[str]:
    role = _effective_role(user).strip().lower()
    if role == UserRole.admin.value:
        return ["clinical", "management", "general"]
    if role == UserRole.manager.value:
        return ["management", "general"]
    return ["clinical", "general"]


def _collection_for_department(department: str) -> str:
    dep = (department or "").strip().lower()
    if dep in {"clinical", "clinician"}:
        return settings.clinical_collection_name
    if dep in {"management", "manager"}:
        return settings.manager_collection_name
    return settings.default_collection_name


@router.get("/collections")
async def list_collections(user=Depends(get_current_user)):
    allowed = _allowed_departments(user)
    labels = {
        "clinical": "Clinical Index",
        "management": "Management Index",
        "general": "General Index",
    }
    return [
        {
            "department": dep,
            "collection": _collection_for_department(dep),
            "label": labels.get(dep, dep),
        }
        for dep in allowed
    ]


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
