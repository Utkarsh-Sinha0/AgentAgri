"""Crop-cycle stage progression from seeded playbooks."""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CropCycle, Farmer, Field
from app.models_memory import MemoryAtom
from app.services import universal_kb
from app.utils.time import utc_now


def _as_date(value) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return None


def stage_for_day(crop: str, days_since_sowing: int) -> tuple[str | None, dict | None]:
    playbook = universal_kb.get_playbook(crop) or {}
    elapsed = 0
    stages = playbook.get("stages") or []
    for stage in stages:
        elapsed += int(stage.get("duration_days") or 0)
        if days_since_sowing <= elapsed:
            return stage.get("name"), stage
    if stages:
        last = stages[-1]
        return last.get("name"), last
    return None, None


async def advance_stage(
    db: AsyncSession,
    cycle: CropCycle,
    today: date | None = None,
    write_atom: bool = True,
) -> str | None:
    sowing = _as_date(cycle.sowing_date)
    if sowing is None:
        return None
    target_stage, guidance = stage_for_day(cycle.crop_name, ((today or utc_now().date()) - sowing).days)
    if not target_stage or target_stage == cycle.current_stage:
        return None

    previous = cycle.current_stage
    cycle.current_stage = target_stage
    if not write_atom:
        return target_stage

    field = await db.get(Field, cycle.field_id)
    farmer = await db.get(Farmer, field.farmer_id) if field else None
    db.add(MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id if farmer else "system",
        field_id=cycle.field_id,
        crop_cycle_id=cycle.id,
        atom_type="playbook_stage_progress",
        summary=f"{cycle.crop_name} moved from {previous or 'unknown'} to {target_stage}",
        details={
            "previous_stage": previous,
            "current_stage": target_stage,
            "objective": (guidance or {}).get("objective"),
            "actions": (guidance or {}).get("actions", [])[:5],
        },
        confidence=0.90,
        source_type="crop_playbook",
        source_id=cycle.crop_name,
        village=getattr(farmer, "village", None),
        tehsil=getattr(farmer, "tehsil", None),
        district=getattr(farmer, "district", None),
        state=getattr(farmer, "state", None),
        event_at=utc_now(),
        is_private=True,
    ))
    return target_stage


def stage_message_hi(cycle: CropCycle, stage: str) -> str:
    guidance = universal_kb.get_stage_guidance(cycle.crop_name, stage)
    actions = (guidance or {}).get("actions", [])[:3] if isinstance(guidance, dict) else []
    lines = [f"🌱 आपकी {cycle.crop_name} फसल अब *{stage}* अवस्था में है।"]
    if actions:
        lines.append("आज की प्राथमिक बातें:")
        lines.extend(f"  {i}. {a}" for i, a in enumerate(actions, 1))
    return "\n".join(lines)
