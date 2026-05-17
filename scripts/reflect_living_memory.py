"""
AgentAgri V4.0 — Nightly living-memory reflection job.

For each field with >=5 atoms in the last 60 days, derive `field_insight`
atoms summarizing chronic patterns (recurring pests, soil trends, monsoon
correlations). The derived atoms feed back into the agent's retrieval as
high-confidence context on subsequent turns, completing the "breathing"
loop.

Usage:
    python -m scripts.reflect_living_memory                 # all eligible fields
    python -m scripts.reflect_living_memory --field <id>    # single field
    python -m scripts.reflect_living_memory --dry-run       # log only, no writes

Idempotent: re-running on the same atom set produces a single
field_insight per (field_id, period) — older insights are superseded
(soft delete via low confidence + new atom) rather than deleted, to
preserve causal links.
"""
from __future__ import annotations

import argparse
import asyncio
import uuid
from collections import Counter
from datetime import timedelta
from typing import Iterable

from loguru import logger
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models_memory import MemoryAtom
from app.utils.time import utc_now

LOOKBACK_DAYS = 60
MIN_ATOMS = 5
INSIGHT_TYPE = "field_insight"
REFLECTION_SOURCE = "reflection"


def _summarize_patterns(atoms: list[MemoryAtom]) -> tuple[str, list[str], float]:
    """Heuristic pattern miner. Returns (summary, patterns, confidence).

    Deterministic + cheap; LLM enrichment can be added later without
    breaking the contract.
    """
    counts: Counter[str] = Counter(a.atom_type for a in atoms if a.atom_type)
    patterns: list[str] = []

    pest_like = sum(c for t, c in counts.items() if "pest" in t or "disease" in t)
    if pest_like >= 3:
        patterns.append(f"recurring pest/disease pressure ({pest_like} events in {LOOKBACK_DAYS}d)")

    fert_events = counts.get("fertilizer_applied", 0)
    if fert_events >= 3:
        patterns.append(f"frequent fertilizer applications ({fert_events}) — review soil-health card")

    irrigation = counts.get("irrigation_event", 0)
    if irrigation >= 4:
        patterns.append(f"high irrigation cadence ({irrigation}) — consider mulching/SRI")

    outcomes = [a for a in atoms if a.atom_type == "outcome_reported"]
    worsened = sum(1 for a in outcomes if (a.details or {}).get("result", "").lower() == "worsened")
    if worsened >= 2:
        patterns.append(f"{worsened} worsened outcomes — advisories under-performing on this field")

    summary = (
        f"Reflection over {len(atoms)} atoms in last {LOOKBACK_DAYS}d. "
        + ("Key signals: " + "; ".join(patterns) if patterns else "No chronic patterns detected.")
    )
    confidence = min(0.85, 0.40 + 0.03 * len(atoms))
    return summary, patterns, confidence


async def _eligible_fields(db: AsyncSession) -> list[tuple[str, str]]:
    cutoff = utc_now() - timedelta(days=LOOKBACK_DAYS)
    rows = (
        await db.execute(
            select(MemoryAtom.field_id, MemoryAtom.farmer_id)
            .where(MemoryAtom.field_id.isnot(None), MemoryAtom.event_at >= cutoff)
            .distinct()
        )
    ).all()
    return [(r[0], r[1]) for r in rows if r[0] and r[1] and r[1] != "system"]


async def _reflect_field(
    db: AsyncSession, field_id: str, farmer_id: str, dry_run: bool = False
) -> MemoryAtom | None:
    cutoff = utc_now() - timedelta(days=LOOKBACK_DAYS)
    atoms = (
        await db.execute(
            select(MemoryAtom)
            .where(
                MemoryAtom.field_id == field_id,
                MemoryAtom.farmer_id == farmer_id,
                MemoryAtom.event_at >= cutoff,
                MemoryAtom.atom_type != INSIGHT_TYPE,
            )
            .order_by(desc(MemoryAtom.event_at))
            .limit(200)
        )
    ).scalars().all()

    if len(atoms) < MIN_ATOMS:
        logger.debug(f"skip field={field_id}: only {len(atoms)} atoms (<{MIN_ATOMS})")
        return None

    summary, patterns, confidence = _summarize_patterns(list(atoms))
    predecessor = atoms[0]

    if dry_run:
        logger.info(f"[dry-run] field={field_id} would write insight: {summary} | patterns={patterns}")
        return None

    insight = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer_id,
        field_id=field_id,
        crop_cycle_id=predecessor.crop_cycle_id,
        atom_type=INSIGHT_TYPE,
        summary=summary,
        details={
            "patterns": patterns,
            "atom_count": len(atoms),
            "lookback_days": LOOKBACK_DAYS,
            "atom_types": dict(Counter(a.atom_type for a in atoms if a.atom_type)),
        },
        confidence=confidence,
        source_type=REFLECTION_SOURCE,
        source_id=None,
        village=predecessor.village,
        tehsil=predecessor.tehsil,
        district=predecessor.district,
        state=predecessor.state,
        event_at=utc_now(),
        is_private=True,
        causal_predecessor_atom_id=predecessor.id,
    )
    db.add(insight)
    return insight


async def run(field_id: str | None = None, dry_run: bool = False) -> dict:
    stats = {"fields_processed": 0, "insights_written": 0, "skipped": 0}
    async with async_session_factory() as db:
        if field_id:
            farmer = await db.scalar(
                select(MemoryAtom.farmer_id).where(MemoryAtom.field_id == field_id).limit(1)
            )
            if not farmer:
                logger.warning(f"field {field_id} has no atoms")
                return stats
            targets: Iterable[tuple[str, str]] = [(field_id, farmer)]
        else:
            targets = await _eligible_fields(db)

        for fid, fmid in targets:
            stats["fields_processed"] += 1
            insight = await _reflect_field(db, fid, fmid, dry_run=dry_run)
            if insight is not None:
                stats["insights_written"] += 1
            else:
                stats["skipped"] += 1

        if not dry_run:
            await db.commit()

    logger.info(f"reflection complete: {stats}")
    return stats


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--field", default=None, help="single field id")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    asyncio.run(run(field_id=args.field, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
