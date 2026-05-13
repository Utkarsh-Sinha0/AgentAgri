"""
Demo memory-palace seeding for Telegram and local smoke workflows.

The seed is database-backed: Telegram commands, retrieval memory context,
finance, NDVI, and dashboard stats all read the same rows.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import (
    Advisory,
    AlertCluster,
    AlertStatus,
    CropCalendarTask,
    CropCycle,
    Farmer,
    Field,
    FinanceEntry,
    Observation,
    SatelliteNDVI,
)
from app.utils.security import hash_password

DEFAULT_MEMORY_SEED = settings.seed_dir / "memory_palace.json"
DEMO_SOURCE = "seeded-demo-memory"
DEMO_TEXT_PREFIX = "DEMO_MEMORY:"
DEMO_FINANCE_PREFIX = "DEMO:"


async def seed_demo_memory_palace(
    db: AsyncSession,
    *,
    telegram_user_id: str = "demo_farmer",
    seed_path: Path | None = None,
) -> dict[str, Any]:
    """Create or refresh the rich demo farm bound to a Telegram user id."""
    data = _load_seed(seed_path or DEFAULT_MEMORY_SEED)
    profile = data["field_profile"]

    farmer = await _upsert_farmer(db, telegram_user_id, profile)
    field = await _upsert_field(db, farmer, profile)
    cycle = await _upsert_cycle(db, field, profile)
    await _ensure_crop_calendar(db, cycle)

    observations, advisories = await _seed_observations(db, farmer, field, cycle, data)
    finance_count = await _seed_finance(db, farmer, cycle, data)
    ndvi_count = await _seed_ndvi(db, field, data)
    cluster = await _upsert_cluster(db, profile, data, observations, advisories)

    await db.commit()
    return {
        "farmer_id": farmer.id,
        "field_id": field.id,
        "crop_cycle_id": cycle.id,
        "crop_name": cycle.crop_name,
        "crop_stage": cycle.current_stage,
        "observations": len(observations),
        "advisories": len(advisories),
        "finance_entries": finance_count,
        "ndvi_points": ndvi_count,
        "cluster_id": cluster.id,
    }


def _load_seed(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise FileNotFoundError(f"Demo memory seed not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


async def _upsert_farmer(
    db: AsyncSession,
    telegram_user_id: str,
    profile: dict[str, Any],
) -> Farmer:
    result = await db.execute(select(Farmer).where(Farmer.phone == telegram_user_id))
    farmer = result.scalar_one_or_none()
    if farmer:
        farmer.name = profile["farmer_name"]
        farmer.preferred_language = "hi"
        farmer.district = profile["district"]
        farmer.tehsil = profile["tehsil"]
        farmer.village = profile["village"]
        farmer.is_active = True
        return farmer

    farmer = Farmer(
        id=str(uuid.uuid4()),
        phone=telegram_user_id,
        hashed_password=hash_password(f"telegram:{telegram_user_id}"),
        name=profile["farmer_name"],
        preferred_language="hi",
        district=profile["district"],
        tehsil=profile["tehsil"],
        village=profile["village"],
    )
    db.add(farmer)
    await db.flush()
    return farmer


async def _upsert_field(db: AsyncSession, farmer: Farmer, profile: dict[str, Any]) -> Field:
    result = await db.execute(
        select(Field).where(
            Field.farmer_id == farmer.id,
            Field.name == profile["field_name"],
        )
    )
    field = result.scalar_one_or_none()
    if field:
        field.area_acres = profile["area_acres"]
        field.soil_type = profile["soil_type"]
        field.soil_ph = profile["soil_ph"]
        field.lat = profile["lat"]
        field.lng = profile["lng"]
        field.irrigation_type = profile["irrigation_type"]
        return field

    field = Field(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        name=profile["field_name"],
        area_acres=profile["area_acres"],
        soil_type=profile["soil_type"],
        soil_ph=profile["soil_ph"],
        lat=profile["lat"],
        lng=profile["lng"],
        irrigation_type=profile["irrigation_type"],
    )
    db.add(field)
    await db.flush()
    return field


async def _upsert_cycle(db: AsyncSession, field: Field, profile: dict[str, Any]) -> CropCycle:
    result = await db.execute(
        select(CropCycle).where(
            CropCycle.field_id == field.id,
            CropCycle.crop_name == profile["crop_name"],
            CropCycle.is_active,
        )
    )
    cycle = result.scalar_one_or_none()
    sowing_date = datetime.utcnow() - timedelta(days=int(profile["days_after_sowing"]))
    harvest_date = sowing_date + timedelta(days=120)
    if cycle:
        cycle.variety = profile["variety"]
        cycle.sowing_date = sowing_date
        cycle.expected_harvest_date = harvest_date
        cycle.current_stage = profile["current_stage"]
        return cycle

    cycle = CropCycle(
        id=str(uuid.uuid4()),
        field_id=field.id,
        crop_name=profile["crop_name"],
        variety=profile["variety"],
        sowing_date=sowing_date,
        expected_harvest_date=harvest_date,
        current_stage=profile["current_stage"],
        is_active=True,
    )
    db.add(cycle)
    await db.flush()
    return cycle


async def _ensure_crop_calendar(db: AsyncSession, cycle: CropCycle) -> None:
    count = await db.scalar(
        select(func.count(CropCalendarTask.id)).where(CropCalendarTask.cycle_id == cycle.id)
    )
    if count:
        return

    tasks = [
        ("seedling", "Nursery preparation and seed treatment", 0),
        ("seedling", "Transplant healthy seedlings", 21),
        ("vegetative", "Scout for blast, brown spot, stem borer", 45),
        ("vegetative", "Split fertilizer only after field check", 52),
        ("flowering", "Avoid unsafe sprays during flowering", 65),
        ("harvest", "Check MSP and mandi price before sale", 110),
    ]
    for stage, task_name, days in tasks:
        db.add(
            CropCalendarTask(
                id=str(uuid.uuid4()),
                cycle_id=cycle.id,
                stage=stage,
                task_name=task_name,
                days_from_sowing=days,
            )
        )


async def _seed_observations(
    db: AsyncSession,
    farmer: Farmer,
    field: Field,
    cycle: CropCycle,
    data: dict[str, Any],
) -> tuple[list[Observation], list[Advisory]]:
    existing = await db.scalar(
        select(func.count(Observation.id)).where(
            Observation.farmer_id == farmer.id,
            Observation.text_content.like(f"{DEMO_TEXT_PREFIX}%"),
        )
    )
    if existing:
        result = await db.execute(
            select(Observation).where(
                Observation.farmer_id == farmer.id,
                Observation.text_content.like(f"{DEMO_TEXT_PREFIX}%"),
            )
        )
        observations = list(result.scalars().all())
        advisory_result = await db.execute(
            select(Advisory).where(Advisory.farmer_id == farmer.id)
        )
        return observations, list(advisory_result.scalars().all())

    observations: list[Observation] = []
    advisories: list[Advisory] = []
    for item in data["observations"]:
        created_at = datetime.utcnow() - timedelta(days=int(item["days_ago"]))
        observation = Observation(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            crop_cycle_id=cycle.id,
            field_id=field.id,
            observation_type="text",
            text_content=item["text"],
            outcome_text=item["outcome"],
            outcome_rating=4,
            outcome_logged_at=created_at + timedelta(days=5),
            reported_stage=cycle.current_stage,
            created_at=created_at,
        )
        db.add(observation)
        await db.flush()

        advisory = Advisory(
            id=str(uuid.uuid4()),
            observation_id=observation.id,
            farmer_id=farmer.id,
            risk_level=item["risk_level"],
            confidence=item["confidence"],
            selected_action_indices=[],
            selected_warning_indices=[],
            actions_text=item["actions"],
            warnings_text=item["warnings"],
            contextualization=item["text"].replace(DEMO_TEXT_PREFIX, "").strip(),
            thinking_enabled=False,
            model_used="seeded-demo-memory",
            retrieval_path="memory_seed",
            latency_ms=0,
            evidence_article_ids=item["evidence_article_ids"],
            memory_reference=f"Seeded memory palace: {item['outcome']}",
            created_at=created_at + timedelta(minutes=2),
        )
        db.add(advisory)
        observations.append(observation)
        advisories.append(advisory)

    return observations, advisories


async def _seed_finance(
    db: AsyncSession,
    farmer: Farmer,
    cycle: CropCycle,
    data: dict[str, Any],
) -> int:
    existing = await db.scalar(
        select(func.count(FinanceEntry.id)).where(
            FinanceEntry.farmer_id == farmer.id,
            FinanceEntry.description.like(f"{DEMO_FINANCE_PREFIX}%"),
        )
    )
    if existing:
        return int(existing)

    for entry in data["finance_entries"]:
        db.add(
            FinanceEntry(
                id=str(uuid.uuid4()),
                farmer_id=farmer.id,
                crop_cycle_id=cycle.id,
                entry_type=entry["type"],
                category=entry["category"],
                amount=float(entry["amount"]),
                description=entry["description"],
                recorded_at=datetime.utcnow() - timedelta(days=int(entry["days_ago"])),
            )
        )
    return len(data["finance_entries"])


async def _seed_ndvi(db: AsyncSession, field: Field, data: dict[str, Any]) -> int:
    existing = await db.scalar(
        select(func.count(SatelliteNDVI.id)).where(
            SatelliteNDVI.field_id == field.id,
            SatelliteNDVI.source == DEMO_SOURCE,
        )
    )
    if existing:
        return int(existing)

    for point in data["ndvi_weekly"]:
        db.add(
            SatelliteNDVI(
                id=str(uuid.uuid4()),
                field_id=field.id,
                date=datetime.utcnow() - timedelta(weeks=int(point["weeks_ago"])),
                ndvi_value=float(point["ndvi"]),
                source=DEMO_SOURCE,
                cloud_cover_pct=float(point["cloud_cover_pct"]),
            )
        )
    return len(data["ndvi_weekly"])


async def _upsert_cluster(
    db: AsyncSession,
    profile: dict[str, Any],
    data: dict[str, Any],
    observations: list[Observation],
    advisories: list[Advisory],
) -> AlertCluster:
    alert = data["community_alert"]
    result = await db.execute(
        select(AlertCluster).where(
            AlertCluster.district == profile["district"],
            AlertCluster.tehsil == profile["tehsil"],
            AlertCluster.village == profile["village"],
            AlertCluster.crop_name == profile["crop_name"],
            AlertCluster.issue_category == alert["issue_category"],
        )
    )
    cluster = result.scalar_one_or_none()
    observation_ids = [obs.id for obs in observations]
    advisory_ids = [adv.id for adv in advisories]
    if cluster:
        cluster.observation_ids = observation_ids
        cluster.advisory_ids = advisory_ids
        cluster.farmer_count = int(alert["farmer_count"])
        cluster.severity = float(alert["severity"])
        cluster.status = AlertStatus.PENDING
        cluster.broadcast_message = alert["broadcast_message"]
        return cluster

    cluster = AlertCluster(
        id=str(uuid.uuid4()),
        district=profile["district"],
        tehsil=profile["tehsil"],
        village=profile["village"],
        crop_name=profile["crop_name"],
        issue_category=alert["issue_category"],
        observation_ids=observation_ids,
        advisory_ids=advisory_ids,
        farmer_count=int(alert["farmer_count"]),
        severity=float(alert["severity"]),
        status=AlertStatus.PENDING,
        broadcast_message=alert["broadcast_message"],
    )
    db.add(cluster)
    await db.flush()
    return cluster
