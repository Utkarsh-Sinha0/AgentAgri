"""Wipe a test-farmer row and all dependent data, scoped by telegram_user_id.

Usage:
    python scripts/wipe_test_farmer.py <telegram_user_id> [--yes]

Deletes the Farmer row (matched on ``phone == telegram_user_id``) plus every
row that references it directly. Cascades are usually enough, but we delete
explicitly so the script works on SQLite where ``PRAGMA foreign_keys`` is off
by default.

Dry-run by default; pass ``--yes`` to actually delete.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import delete, select

from app.database import async_session_factory, init_db
from app.models import (
    Advisory,
    CropCalendarTask,
    CropCycle,
    Farmer,
    Field,
    Observation,
    VerifierReport,
)
from app.models_memory import (
    ActionImpact,
    ConversationThread,
    ConversationTurn,
    FarmerProfile,
    MemoryAtom,
    SourceCitation,
)


async def wipe(telegram_id: str, apply: bool) -> int:
    await init_db()
    async with async_session_factory() as db:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == telegram_id))
        if not farmer:
            print(f"No farmer row for telegram_id={telegram_id}. Nothing to wipe.")
            return 0

        field_ids = (
            await db.execute(select(Field.id).where(Field.farmer_id == farmer.id))
        ).scalars().all()
        cycle_ids = (
            await db.execute(
                select(CropCycle.id).where(CropCycle.field_id.in_(field_ids))
            )
        ).scalars().all() if field_ids else []
        advisory_ids = (
            await db.execute(select(Advisory.id).where(Advisory.farmer_id == farmer.id))
        ).scalars().all()
        thread_ids = (
            await db.execute(
                select(ConversationThread.id).where(ConversationThread.farmer_id == farmer.id)
            )
        ).scalars().all()

        print("Will delete:")
        print(f"  Farmer:  1 ({farmer.name!r} phone={farmer.phone!r} district={farmer.district!r})")
        print(f"  Fields:  {len(field_ids)}")
        print(f"  Cycles:  {len(cycle_ids)}")
        print(f"  Advisories: {len(advisory_ids)}")
        print(f"  Threads: {len(thread_ids)}")

        if not apply:
            print("\nDry run. Re-run with --yes to apply.")
            return 0

        # Order: leaves first, then parents.
        if advisory_ids:
            await db.execute(delete(SourceCitation).where(SourceCitation.advisory_id.in_(advisory_ids)))
            await db.execute(delete(VerifierReport).where(VerifierReport.advisory_id.in_(advisory_ids)))
            await db.execute(delete(ActionImpact).where(ActionImpact.advisory_id.in_(advisory_ids)))
        if thread_ids:
            await db.execute(delete(ConversationTurn).where(ConversationTurn.thread_id.in_(thread_ids)))
        await db.execute(delete(ConversationThread).where(ConversationThread.farmer_id == farmer.id))
        await db.execute(delete(MemoryAtom).where(MemoryAtom.farmer_id == farmer.id))
        await db.execute(delete(FarmerProfile).where(FarmerProfile.farmer_id == farmer.id))
        await db.execute(delete(Advisory).where(Advisory.farmer_id == farmer.id))
        await db.execute(delete(Observation).where(Observation.farmer_id == farmer.id))
        if cycle_ids:
            await db.execute(delete(CropCalendarTask).where(CropCalendarTask.cycle_id.in_(cycle_ids)))
            await db.execute(delete(CropCycle).where(CropCycle.id.in_(cycle_ids)))
        if field_ids:
            await db.execute(delete(Field).where(Field.id.in_(field_ids)))
        await db.execute(delete(Farmer).where(Farmer.id == farmer.id))
        await db.commit()
        print("\nDone. Farmer + dependents wiped.")
        return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scripts/wipe_test_farmer.py <telegram_user_id> [--yes]")
        raise SystemExit(2)
    tg_id = sys.argv[1]
    apply = "--yes" in sys.argv[2:]
    raise SystemExit(asyncio.run(wipe(tg_id, apply)))
