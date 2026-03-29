import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, Column, DateTime, Float, String, Text, select
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql import func

from db.postgres import Base


def initial_trust_score() -> float:
    return 1.0


def update_trust_score(current: float, delta: float) -> float:
    return max(0.0, min(2.0, current + delta))


class SourceTrustScore(Base):
    __tablename__ = "source_trust_scores"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source_id = Column(String(255), nullable=False, unique=True)
    source_name = Column(String(255), nullable=True)
    department = Column(String(100), nullable=True)
    trust_score = Column(Float, nullable=False, default=1.0)
    is_deprecated = Column(Boolean, nullable=False, default=False)
    last_updated = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class RoutingPreference(Base):
    __tablename__ = "routing_preferences"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    department = Column(String(100), nullable=False, unique=True)
    preferred_sources = Column(ARRAY(Text), nullable=True)
    avoided_sources = Column(ARRAY(Text), nullable=True)
    updated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


async def upsert_source_trust_score(
    db: AsyncSession,
    *,
    source_id: str,
    department: Optional[str],
    source_name: Optional[str] = None,
    initial_score: Optional[float] = None,
) -> SourceTrustScore:
    result = await db.execute(select(SourceTrustScore).where(SourceTrustScore.source_id == source_id))
    row = result.scalars().first()
    if row is None:
        row = SourceTrustScore(
            source_id=source_id,
            source_name=source_name,
            department=department,
            trust_score=initial_score if initial_score is not None else initial_trust_score(),
            is_deprecated=False,
        )
        db.add(row)
    else:
        row.department = department or row.department
        row.source_name = source_name or row.source_name
        if initial_score is not None:
            row.trust_score = initial_score
        row.last_updated = datetime.utcnow()

    await db.commit()
    await db.refresh(row)
    return row


async def bump_department_trust_scores(db: AsyncSession, department: Optional[str], delta: float) -> int:
    dep = (department or "").strip()
    if not dep:
        return 0
    result = await db.execute(select(SourceTrustScore).where(SourceTrustScore.department == dep))
    rows = list(result.scalars().all())
    for row in rows:
        row.trust_score = update_trust_score(float(row.trust_score), delta)
        row.last_updated = datetime.utcnow()
    if rows:
        await db.commit()
    return len(rows)


async def mark_source_deprecated(db: AsyncSession, source_id: str, is_deprecated: bool = True) -> Optional[SourceTrustScore]:
    result = await db.execute(select(SourceTrustScore).where(SourceTrustScore.source_id == source_id))
    row = result.scalars().first()
    if row is None:
        row = SourceTrustScore(
            source_id=source_id,
            trust_score=initial_trust_score(),
            is_deprecated=is_deprecated,
        )
        db.add(row)
    else:
        row.is_deprecated = is_deprecated
        row.last_updated = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return row


async def update_routing_preferences(
    db: AsyncSession,
    *,
    department: Optional[str],
    prefer_source_id: Optional[str] = None,
    avoid_source_id: Optional[str] = None,
) -> Optional[RoutingPreference]:
    dep = (department or "").strip()
    if not dep:
        return None

    result = await db.execute(select(RoutingPreference).where(RoutingPreference.department == dep))
    row = result.scalars().first()
    if row is None:
        row = RoutingPreference(department=dep, preferred_sources=[], avoided_sources=[])
        db.add(row)

    preferred = list(row.preferred_sources or [])
    avoided = list(row.avoided_sources or [])

    if prefer_source_id:
        if prefer_source_id not in preferred:
            preferred.append(prefer_source_id)
        if prefer_source_id in avoided:
            avoided.remove(prefer_source_id)

    if avoid_source_id:
        if avoid_source_id not in avoided:
            avoided.append(avoid_source_id)
        if avoid_source_id in preferred:
            preferred.remove(avoid_source_id)

    row.preferred_sources = preferred
    row.avoided_sources = avoided
    row.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(row)
    return row
