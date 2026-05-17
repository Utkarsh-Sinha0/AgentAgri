"""Next-crop recommendation from field history and MSP seed data."""
from __future__ import annotations

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CropCycle, Farmer, Field
from app.models_memory import MemoryAtom
from app.services import universal_kb

CEREALS = {"rice", "wheat", "maize", "paddy"}
LEGUMES = {"gram", "chickpea", "lentil", "moong", "urad", "arhar", "pigeonpea"}


def _family(crop: str) -> str:
    c = (crop or "").lower()
    if c in CEREALS:
        return "cereal"
    if c in LEGUMES:
        return "legume"
    return c or "unknown"


async def suggest_next_crop(db: AsyncSession, field_id: str) -> dict:
    cycles = (
        await db.execute(
            select(CropCycle)
            .where(CropCycle.field_id == field_id)
            .order_by(desc(CropCycle.sowing_date), desc(CropCycle.created_at))
            .limit(3)
        )
    ).scalars().all()
    last = cycles[0].crop_name.lower() if cycles else ""
    field = await db.get(Field, field_id)
    farmer = await db.get(Farmer, field.farmer_id) if field else None
    state = getattr(farmer, "state", None) or "Bihar"

    candidates = ["arhar", "wheat", "maize"] if _family(last) == "cereal" else ["rice", "wheat", "maize"]
    msp = {r.get("crop"): r.get("msp_rs_per_quintal") for c in candidates for r in universal_kb.get_msp(c, state=state)}
    ranked = sorted(candidates, key=lambda c: (msp.get(c) or 0, c), reverse=True)

    insight = await db.scalar(
        select(MemoryAtom.summary)
        .where(MemoryAtom.field_id == field_id, MemoryAtom.atom_type == "field_insight")
        .order_by(desc(MemoryAtom.event_at))
        .limit(1)
    )
    return {
        "next_crop": ranked[0],
        "alternatives": ranked[1:3],
        "reasoning": [
            f"Last crop was {last or 'unknown'}; avoid repeating the same crop family.",
            "Legume after cereal improves nitrogen balance." if _family(last) == "cereal" else "Cereal is acceptable after a legume/other crop.",
            f"MSP context checked for {state}.",
            f"Field memory: {insight}" if insight else "No strong field-memory constraint found.",
        ],
    }
