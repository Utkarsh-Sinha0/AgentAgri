"""
Outbreak warning service — 10%-threshold pest/disease cascade.

Covers:
- Threshold met at village scope creates an alert and excludes reporters
  from the target list.
- Threshold not met returns None and writes nothing.
- Scope cascade: village checked before tehsil/district.
- Existing live alert is reused, not duplicated.
- mark_outbreak_notified / mark_outbreak_consumed dedup behaviour.
- fetch_pending_outbreak_for_farmer surfaces an alert exactly once.
"""
from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import select

from app.models import AlertCluster, AlertKind, CropCycle, Farmer, Field, Observation
from app.services.outbreak import (
    OUTBREAK_THRESHOLD,
    check_and_trigger_outbreak,
    fetch_pending_outbreak_for_farmer,
    format_warning_message,
    list_target_farmers,
    mark_outbreak_consumed,
    mark_outbreak_notified,
)
from app.utils.security import hash_password
from app.utils.time import utc_now


async def _seed_farmer(
    db,
    *,
    fid: str,
    village: str,
    tehsil: str = "Tarapur",
    district: str = "Munger",
    crop: str = "rice",
) -> Farmer:
    farmer = Farmer(
        id=fid,
        phone=f"tg-{fid}",
        hashed_password=hash_password("x"),
        name=fid,
        village=village,
        tehsil=tehsil,
        district=district,
    )
    field = Field(id=f"{fid}-fld", farmer_id=fid, name="A", area_acres=1.0)
    cycle = CropCycle(
        id=f"{fid}-cyc",
        field_id=field.id,
        crop_name=crop,
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    db.add_all([farmer, field, cycle])
    await db.flush()
    return farmer


async def _seed_negative_outcome(db, farmer: Farmer, *, threat: str = "blast"):
    obs = Observation(
        id=f"obs-{farmer.id}",
        farmer_id=farmer.id,
        crop_cycle_id=f"{farmer.id}-cyc",
        field_id=f"{farmer.id}-fld",
        observation_type="text",
        text_content=f"my rice leaves show {threat}",
        outcome_text=f"worsened: {threat} got worse",
        outcome_rating=1,
        outcome_logged_at=utc_now(),
    )
    db.add(obs)
    await db.flush()
    return obs


async def test_threshold_met_creates_outbreak_at_village_scope(db_session):
    # 10 farmers in village Khagaria; 2 (= 20%) report blast → 10% trips.
    farmers = []
    for i in range(10):
        farmers.append(await _seed_farmer(db_session, fid=f"f-{i}", village="Khagaria"))

    # Two reporters: f-0 and f-1.
    await _seed_negative_outcome(db_session, farmers[0])
    await _seed_negative_outcome(db_session, farmers[1])
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session,
        reporter_farmer_id="f-1",
        crop_name="rice",
        pest_or_disease="blast",
    )

    assert alert is not None
    assert alert.kind == AlertKind.OUTBREAK
    assert alert.scope == "village"
    assert alert.village == "Khagaria"
    assert alert.pest_or_disease == "blast"
    assert set(alert.reporting_farmer_ids) >= {"f-0", "f-1"}
    assert (len(alert.reporting_farmer_ids) / 10) >= OUTBREAK_THRESHOLD

    targets = await list_target_farmers(db_session, alert)
    target_ids = {f.id for f in targets}
    # Reporters are excluded from targets.
    assert "f-0" not in target_ids and "f-1" not in target_ids
    # Other 8 same-village rice farmers are in.
    assert {f"f-{i}" for i in range(2, 10)} <= target_ids


async def test_threshold_not_met_returns_none(db_session):
    # 50 farmers, only 1 report → 2% → below 10%.
    for i in range(50):
        await _seed_farmer(db_session, fid=f"g-{i}", village="Khagaria")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "g-0")
    ))
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session,
        reporter_farmer_id="g-0",
        crop_name="rice",
        pest_or_disease="blast",
    )
    assert alert is None
    count = (await db_session.execute(select(AlertCluster))).scalars().all()
    assert count == []


async def test_scope_cascade_picks_village_not_district(db_session):
    # 4 farmers in target village, 2 report (50%) → village trips.
    # 30 farmers elsewhere in district, none report → district would not trip.
    for i in range(4):
        await _seed_farmer(db_session, fid=f"v-{i}", village="Khagaria")
    for i in range(30):
        await _seed_farmer(db_session, fid=f"d-{i}", village=f"Other-{i}")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "v-0")
    ))
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "v-1")
    ))
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session,
        reporter_farmer_id="v-1",
        crop_name="rice",
        pest_or_disease="blast",
    )
    assert alert is not None
    assert alert.scope == "village"
    assert alert.village == "Khagaria"


async def test_existing_alert_is_reused_not_duplicated(db_session):
    for i in range(5):
        await _seed_farmer(db_session, fid=f"r-{i}", village="Khagaria")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "r-0")
    ))
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "r-1")
    ))
    await db_session.commit()

    a1 = await check_and_trigger_outbreak(
        db_session, reporter_farmer_id="r-1", crop_name="rice", pest_or_disease="blast"
    )
    assert a1 is not None

    # Second call from a third reporter with the same threat should reuse a1.
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "r-2")
    ))
    await db_session.commit()
    a2 = await check_and_trigger_outbreak(
        db_session, reporter_farmer_id="r-2", crop_name="rice", pest_or_disease="blast"
    )
    assert a2 is not None
    assert a2.id == a1.id
    assert "r-2" in set(a2.reporting_farmer_ids)

    all_alerts = (await db_session.execute(select(AlertCluster))).scalars().all()
    assert len(all_alerts) == 1


async def test_fetch_pending_surfaces_once_per_farmer(db_session):
    for i in range(5):
        await _seed_farmer(db_session, fid=f"p-{i}", village="Khagaria")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "p-0")
    ))
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "p-1")
    ))
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session, reporter_farmer_id="p-1", crop_name="rice", pest_or_disease="blast"
    )
    assert alert is not None

    target = await db_session.scalar(select(Farmer).where(Farmer.id == "p-3"))
    await mark_outbreak_notified(db_session, alert, [target.id])

    pending = await fetch_pending_outbreak_for_farmer(db_session, target.id)
    assert pending is not None and pending.id == alert.id

    await mark_outbreak_consumed(db_session, pending, target.id)

    # Second call must return None — already consumed.
    pending2 = await fetch_pending_outbreak_for_farmer(db_session, target.id)
    assert pending2 is None


async def test_reporters_are_not_in_pending_queue(db_session):
    for i in range(5):
        await _seed_farmer(db_session, fid=f"x-{i}", village="Khagaria")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "x-0")
    ))
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "x-1")
    ))
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session, reporter_farmer_id="x-1", crop_name="rice", pest_or_disease="blast"
    )
    assert alert is not None
    targets = await list_target_farmers(db_session, alert)
    assert all(f.id not in {"x-0", "x-1"} for f in targets)


async def test_expired_alert_is_not_resurfaced(db_session):
    for i in range(5):
        await _seed_farmer(db_session, fid=f"e-{i}", village="Khagaria")
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "e-0")
    ))
    await _seed_negative_outcome(db_session, await db_session.scalar(
        select(Farmer).where(Farmer.id == "e-1")
    ))
    await db_session.commit()

    alert = await check_and_trigger_outbreak(
        db_session, reporter_farmer_id="e-1", crop_name="rice", pest_or_disease="blast"
    )
    assert alert is not None
    alert.expires_at = utc_now() - timedelta(days=1)
    await mark_outbreak_notified(db_session, alert, ["e-3"])
    await db_session.commit()

    pending = await fetch_pending_outbreak_for_farmer(db_session, "e-3")
    assert pending is None


def test_format_warning_message_is_bilingual_and_specific():
    alert = AlertCluster(
        id="x", kind=AlertKind.OUTBREAK, scope="village",
        crop_name="rice", pest_or_disease="blast", village="Khagaria",
    )
    msg = format_warning_message(alert)
    assert "Blast" in msg
    assert "Rice" in msg
    assert "Khagaria" in msg
    assert "चेतावनी" in msg  # Hindi
    assert "warning" in msg.lower()  # English
