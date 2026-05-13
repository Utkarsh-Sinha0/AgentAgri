from __future__ import annotations

from sqlalchemy import func, select

from app.models import Advisory, AlertCluster, Farmer, FinanceEntry, Observation, SatelliteNDVI
from app.services.demo_seed import seed_demo_memory_palace


async def test_demo_memory_palace_seed_is_rich_and_idempotent(db_session):
    first = await seed_demo_memory_palace(db_session, telegram_user_id="telegram-demo-test")
    second = await seed_demo_memory_palace(db_session, telegram_user_id="telegram-demo-test")

    assert first["farmer_id"] == second["farmer_id"]
    assert first["field_id"] == second["field_id"]
    assert first["crop_cycle_id"] == second["crop_cycle_id"]
    assert first["observations"] >= 5
    assert first["advisories"] >= 5
    assert first["finance_entries"] >= 5
    assert first["ndvi_points"] >= 8

    farmer_count = await db_session.scalar(
        select(func.count(Farmer.id)).where(Farmer.phone == "telegram-demo-test")
    )
    observation_count = await db_session.scalar(
        select(func.count(Observation.id)).where(Observation.farmer_id == first["farmer_id"])
    )
    advisory_count = await db_session.scalar(
        select(func.count(Advisory.id)).where(Advisory.farmer_id == first["farmer_id"])
    )
    finance_count = await db_session.scalar(
        select(func.count(FinanceEntry.id)).where(FinanceEntry.farmer_id == first["farmer_id"])
    )
    ndvi_count = await db_session.scalar(
        select(func.count(SatelliteNDVI.id)).where(SatelliteNDVI.field_id == first["field_id"])
    )
    cluster_count = await db_session.scalar(select(func.count(AlertCluster.id)))

    assert farmer_count == 1
    assert observation_count == first["observations"]
    assert advisory_count == first["advisories"]
    assert finance_count == first["finance_entries"]
    assert ndvi_count == first["ndvi_points"]
    assert cluster_count == 1
