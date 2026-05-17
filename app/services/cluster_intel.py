"""Privacy-safe aggregate farmer intelligence."""
from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CropCycle, Farmer, Field
from app.models_memory import MemoryAtom, MemorySummary
from app.utils.time import utc_now


async def district_stage_distribution(db: AsyncSession, crop: str, district: str, min_farmers: int = 3) -> dict | None:
    rows = (
        await db.execute(
            select(CropCycle.current_stage, func.count(func.distinct(Field.farmer_id)))
            .join(Field, Field.id == CropCycle.field_id)
            .join(Farmer, Farmer.id == Field.farmer_id)
            .where(
                Farmer.district == district,
                CropCycle.is_active.is_(True),
                func.lower(CropCycle.crop_name) == crop.lower(),
            )
            .group_by(CropCycle.current_stage)
        )
    ).all()
    total = sum(int(count) for _, count in rows)
    if total < min_farmers:
        return None
    return {"crop": crop, "district": district, "total_farmers": total, "stages": {stage or "unknown": int(count) for stage, count in rows}}


async def district_pest_pressure(db: AsyncSession, crop: str, district: str, min_farmers: int = 3) -> dict | None:
    cutoff = utc_now() - timedelta(days=30)
    atoms = (
        await db.execute(
            select(MemoryAtom)
            .where(
                MemoryAtom.district == district,
                MemoryAtom.event_at >= cutoff,
                MemoryAtom.atom_type.in_(["disease_observed", "pest_detected", "advisory_given", "outcome_reported"]),
                MemoryAtom.is_shareable.is_(True),
                MemoryAtom.redacted.is_(False),
            )
            .limit(300)
        )
    ).scalars().all()
    farmers = {a.farmer_id for a in atoms if a.farmer_id and a.farmer_id != "system"}
    if len(farmers) < min_farmers:
        return None
    counts = Counter(a.atom_type for a in atoms)
    return {"crop": crop, "district": district, "farmer_count": len(farmers), "signals": dict(counts)}


async def latest_summary(db: AsyncSession, scale: str, scale_id: str) -> dict | None:
    summary = await db.scalar(
        select(MemorySummary)
        .where(MemorySummary.scale == scale, MemorySummary.scale_id == scale_id)
        .order_by(MemorySummary.last_updated.desc())
        .limit(1)
    )
    if not summary:
        return None
    return {
        "scale": summary.scale,
        "scale_id": summary.scale_id,
        "summary": summary.summary_text,
        "patterns": summary.key_patterns or [],
        "farmer_count": summary.farmer_count,
    }
