from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

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


@router.post("/register", response_model=TokenResponse)
async def register(payload: RegisterRequest, db: AsyncSession = Depends(get_db)):
    existing = await get_user_by_email(db, payload.email)
    if existing:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Email already registered")

    user = User(
        email=payload.email,
        password_hash=hash_password(payload.password),
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
