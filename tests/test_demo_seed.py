from __future__ import annotations

from sqlalchemy import func, select

from app.models import Advisory, AlertCluster, CropCycle, Farmer, Field, FinanceEntry, Observation, SatelliteNDVI
from app.services.demo_seed import graft_demo_farmer, seed_demo_memory_palace


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


async def test_per_step_graft_mutates_fields_in_order(db_session):
    """Walk every reg step's graft call; assert each one only mutates its own field."""
    judge_id = "judge-walk-test"
    await seed_demo_memory_palace(db_session, telegram_user_id="demo_farmer")

    # name → also rebinds phone from "demo_farmer" to judge_id
    r = await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"name": "Anita Singh"})
    assert r["status"] == "ok"
    f = (await db_session.execute(select(Farmer).where(Farmer.phone == judge_id))).scalar_one()
    assert f.name == "Anita Singh"

    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"district": "Patna"})
    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"pincode": "800001"})
    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"tehsil": "Phulwari"})
    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"village": "Naubatpur"})
    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"soil_type": "clay", "area_acres": 3.5})
    await graft_demo_farmer(db_session, telegram_user_id=judge_id, partial_profile={"crop_name": "wheat"})

    await db_session.refresh(f)
    assert f.district == "Patna"
    assert f.pincode == "800001"
    assert f.tehsil == "Phulwari"
    assert f.village == "Naubatpur"

    field = (await db_session.execute(select(Field).where(Field.farmer_id == f.id))).scalars().first()
    assert field is not None
    await db_session.refresh(field)
    assert field.soil_type == "clay"
    assert float(field.area_acres) == 3.5

    cycle = (
        await db_session.execute(
            select(CropCycle).where(CropCycle.field_id == field.id, CropCycle.is_active)
        )
    ).scalars().first()
    assert cycle is not None
    await db_session.refresh(cycle)
    assert cycle.crop_name == "wheat"

    # Seeded alert clusters should follow the judge's geography so the
    # pre-baked outbreak covers wherever they registered.
    from app.models import AlertCluster, AlertStatus
    clusters = (
        await db_session.execute(
            select(AlertCluster).where(AlertCluster.status == AlertStatus.PENDING)
        )
    ).scalars().all()
    assert clusters, "expected at least one pending cluster from seed"
    rice_cluster = next((c for c in clusters if (c.crop_name or "").lower() == "wheat"), None)
    # The primary cluster started as 'rice' and should now read 'wheat'.
    assert rice_cluster is not None, "primary cluster should be re-homed to judge's crop"
    assert rice_cluster.village == "Naubatpur"
    assert rice_cluster.tehsil == "Phulwari"
    assert rice_cluster.district == "Patna"
    assert rice_cluster.pincode == "800001"
