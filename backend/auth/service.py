from datetime import datetime, timedelta
from typing import Optional

from jose import jwt
from passlib.context import CryptContext
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import get_settings
from .models import User, UserRole


pwd_context = CryptContext(schemes=["argon2"], deprecated="auto")
settings = get_settings()


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def verify_password(password: str, hashed: str) -> bool:
    return pwd_context.verify(password, hashed)


def create_access_token(*, user_id: str, role, department: Optional[str]) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.access_token_exp_minutes)
    role_value = role.value if hasattr(role, "value") else str(role)
    payload = {"sub": str(user_id), "role": role_value, "department": department, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def create_refresh_token(*, user_id: str) -> str:
    expire = datetime.utcnow() + timedelta(days=settings.refresh_token_exp_days)
    payload = {"sub": str(user_id), "type": "refresh", "exp": expire}
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


async def get_user_by_email(db: AsyncSession, email: str) -> Optional[User]:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalars().first()


async def store_refresh_hash(db: AsyncSession, user_id: str, token_hash: str) -> None:
    await db.execute(update(User).where(User.id == user_id).values(refresh_token_hash=token_hash))
    await db.commit()
