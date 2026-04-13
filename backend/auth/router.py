from fastapi import APIRouter, Depends, HTTPException, Query, status
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from config import get_settings
from auth.dependencies import oauth2_scheme
from auth.dependencies import get_current_user, require_role
from db.postgres import get_db
from .models import RegisterRequest, LoginRequest, TokenResponse, User, UserRole
from .service import (
    create_access_token,
    create_refresh_token,
    get_user_by_email,
    hash_password,
    store_refresh_hash,
    verify_password,
)


router = APIRouter()
settings = get_settings()
ALLOWED_REGISTRATION_ROLES = {UserRole.manager.value, UserRole.clinician.value, UserRole.admin.value}


@router.post("/register", response_model=TokenResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    role_value = payload.role.value if hasattr(payload.role, "value") else str(payload.role)
    if role_value not in ALLOWED_REGISTRATION_ROLES:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Role must be one of: manager, clinician, admin",
        )

    existing = await get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered")

    try:
        password_hash = hash_password(payload.password)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc))

    user = User(
        email=payload.email,
        password_hash=password_hash,
        full_name=payload.full_name,
        role=payload.role,
        department=payload.department,
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)

    access_token = create_access_token(user_id=user.id, role=user.role, department=user.department)
    refresh_token = create_refresh_token(user_id=user.id)
    await store_refresh_hash(db, user.id, hash_password(refresh_token))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/login", response_model=TokenResponse)
async def login(payload: LoginRequest, db: AsyncSession = Depends(get_db)):
    user = await get_user_by_email(db, payload.email)
    if not user or not verify_password(payload.password, user.password_hash):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid credentials")

    access_token = create_access_token(user_id=user.id, role=user.role, department=user.department)
    refresh_token = create_refresh_token(user_id=user.id)
    await store_refresh_hash(db, user.id, hash_password(refresh_token))

    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.post("/refresh", response_model=TokenResponse)
async def refresh_token(token: str = Depends(oauth2_scheme), db: AsyncSession = Depends(get_db)):
    credentials_exception = HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid refresh token")
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id: str | None = payload.get("sub")
        token_type = payload.get("type")
        if not user_id or token_type != "refresh":
            raise credentials_exception
    except JWTError:
        raise credentials_exception

    user = await db.get(User, user_id)
    if not user or not user.refresh_token_hash:
        raise credentials_exception
    if not verify_password(token, user.refresh_token_hash):
        raise credentials_exception

    access_token = create_access_token(user_id=user.id, role=user.role, department=user.department)
    refresh_token = create_refresh_token(user_id=user.id)
    await store_refresh_hash(db, user.id, hash_password(refresh_token))
    return TokenResponse(access_token=access_token, refresh_token=refresh_token)


@router.get("/me")
async def me(user=Depends(get_current_user)):
    return {
        "id": str(user.id),
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role.value if hasattr(user.role, "value") else str(user.role),
        "department": user.department,
        "is_active": getattr(user, "is_active", True),
        "created_at": user.created_at.isoformat() if user.created_at else None,
    }


@router.get("/users")
async def list_users(
    roles: str | None = Query(default=None, description="Comma-separated roles to include"),
    db: AsyncSession = Depends(get_db),
    _user=Depends(require_role(UserRole.manager, UserRole.admin)),
):
    allowed = {UserRole.manager.value, UserRole.admin.value, UserRole.clinician.value}
    selected_roles = [UserRole.manager.value, UserRole.admin.value]
    if roles:
        parsed = [r.strip().lower() for r in roles.split(",") if r.strip()]
        filtered = [r for r in parsed if r in allowed]
        if filtered:
            selected_roles = filtered

    stmt = (
        select(User)
        .where(User.is_active.is_(True))
        .where(User.role.in_(selected_roles))
        .order_by(User.full_name.asc().nulls_last(), User.email.asc())
    )
    result = await db.execute(stmt)
    users = list(result.scalars().all())
    return [
        {
            "id": str(u.id),
            "email": u.email,
            "full_name": u.full_name,
            "role": u.role.value if hasattr(u.role, "value") else str(u.role),
            "department": u.department,
        }
        for u in users
    ]
