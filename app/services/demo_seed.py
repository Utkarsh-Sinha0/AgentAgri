"""
Demo memory-palace seeding for Telegram and local smoke workflows.

The seed is database-backed: Telegram commands, retrieval memory context,
finance, NDVI, and dashboard stats all read the same rows.
"""
from __future__ import annotations

import json
import uuid
from datetime import timedelta
from pathlib import Path
from typing import Any

from loguru import logger
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
from app.utils.time import utc_now

DEFAULT_MEMORY_SEED = settings.seed_dir / "memory_palace.json"
DEEP_MEMORY_SEED = settings.seed_dir / "demo_farmer_deep.json"
DEMO_SOURCE = "seeded-demo-memory"
DEMO_DEEP_SOURCE = "seeded-demo-memory-deep"
DEMO_TEXT_PREFIX = "DEMO_MEMORY:"
DEMO_FINANCE_PREFIX = "DEMO:"

# Fields that /start registration is allowed to overwrite on the demo farmer.
GRAFTABLE_FIELDS = {"name", "village", "tehsil", "district", "pincode", "preferred_language"}
GRAFTABLE_FIELD_FIELDS = {"crop_name", "area_acres", "soil_type", "irrigation_type"}


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
        farmer.pincode = profile.get("pincode", "811201")
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
        pincode=profile.get("pincode", "811201"),
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
    sowing_date = utc_now() - timedelta(days=int(profile["days_after_sowing"]))
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
        created_at = utc_now() - timedelta(days=int(item["days_ago"]))
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
                recorded_at=utc_now() - timedelta(days=int(entry["days_ago"])),
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
                date=utc_now() - timedelta(weeks=int(point["weeks_ago"])),
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


# ─────────────────────────────────────────────────────────────────────────────
# Deep demo layer — 180-day rich seed for Gemma 4 capability showcase.
# Additive on top of seed_demo_memory_palace(). Idempotent.
# ─────────────────────────────────────────────────────────────────────────────

async def seed_demo_deep_layer(
    db: AsyncSession,
    *,
    telegram_user_id: str = "demo_farmer",
    seed_path: Path | None = None,
) -> dict[str, Any]:
    """Layer the 180-day deep profile on top of the base demo farmer.

    Looks up the existing demo farmer + field + active cycle (created by
    seed_demo_memory_palace) and adds: extra observations across many
    modalities, longer NDVI history with a stress dip, more finance rows,
    three village/district alert clusters, and a future-dated outbreak.

    Idempotent: every insert is guarded by a DEMO_DEEP_SOURCE / prefix check.
    """
    path = seed_path or DEEP_MEMORY_SEED
    if not path.exists():
        logger.warning(f"Deep demo seed not found at {path}; skipping deep layer")
        return {"status": "skipped", "reason": "deep_seed_missing"}

    data = _load_seed(path)

    farmer_q = await db.execute(select(Farmer).where(Farmer.phone == telegram_user_id))
    farmer = farmer_q.scalar_one_or_none()
    if not farmer:
        logger.warning(f"Demo farmer {telegram_user_id} not found; run base seeder first")
        return {"status": "skipped", "reason": "base_farmer_missing"}

    field_q = await db.execute(select(Field).where(Field.farmer_id == farmer.id))
    field = field_q.scalars().first()
    cycle_q = await db.execute(
        select(CropCycle).where(CropCycle.field_id == field.id, CropCycle.is_active)
    )
    cycle = cycle_q.scalars().first()
    if not field or not cycle:
        logger.warning("Demo field/cycle missing; run base seeder first")
        return {"status": "skipped", "reason": "base_field_or_cycle_missing"}

    overlay = data.get("field_profile_overlay") or {}
    if overlay:
        if "current_stage" in overlay:
            cycle.current_stage = overlay["current_stage"]
        if "sowing_offset_days" in overlay:
            cycle.sowing_date = utc_now() + timedelta(days=int(overlay["sowing_offset_days"]))
        if "expected_harvest_offset_days" in overlay:
            cycle.expected_harvest_date = utc_now() + timedelta(
                days=int(overlay["expected_harvest_offset_days"])
            )

    obs_added = await _seed_deep_observations(db, farmer, field, cycle, data)
    ndvi_added = await _seed_deep_ndvi(db, field, data)
    fin_added = await _seed_deep_finance(db, farmer, cycle, data)
    clusters_added = await _seed_extra_clusters(db, data)

    await db.commit()
    return {
        "status": "ok",
        "farmer_id": farmer.id,
        "observations_added": obs_added,
        "ndvi_added": ndvi_added,
        "finance_added": fin_added,
        "extra_clusters_added": clusters_added,
    }


async def _seed_deep_observations(
    db: AsyncSession,
    farmer: Farmer,
    field: Field,
    cycle: CropCycle,
    data: dict[str, Any],
) -> int:
    existing = await db.scalar(
        select(func.count(Observation.id)).where(
            Observation.farmer_id == farmer.id,
            Observation.text_content.like(f"{DEMO_TEXT_PREFIX}%"),
            Observation.observation_type.in_(["text", "voice", "photo"]),
            Observation.text_content.like("%DEMO_MEMORY%"),
        )
    )
    items = data.get("extra_observations") or []
    if existing and int(existing) >= len(items) + 5:
        return 0  # base + deep already seeded
    inserted = 0
    for item in items:
        text = item.get("text", "")
        dup_q = await db.execute(
            select(Observation.id).where(
                Observation.farmer_id == farmer.id,
                Observation.text_content == text,
            )
        )
        if dup_q.scalar_one_or_none():
            continue
        created_at = utc_now() - timedelta(days=int(item["days_ago"]))
        modality = item.get("modality", "text")
        obs = Observation(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            crop_cycle_id=cycle.id,
            field_id=field.id,
            observation_type=modality,
            text_content=text,
            outcome_text=item.get("outcome"),
            outcome_rating=4,
            outcome_logged_at=created_at + timedelta(days=3),
            reported_stage=cycle.current_stage,
            created_at=created_at,
        )
        db.add(obs)
        await db.flush()
        advisory = Advisory(
            id=str(uuid.uuid4()),
            observation_id=obs.id,
            farmer_id=farmer.id,
            risk_level=item.get("risk_level", "NORMAL"),
            confidence=item.get("confidence", "MEDIUM"),
            selected_action_indices=[],
            selected_warning_indices=[],
            actions_text=item.get("actions", []),
            warnings_text=item.get("warnings", []),
            contextualization=text.replace(DEMO_TEXT_PREFIX, "").strip(),
            thinking_enabled=False,
            model_used=DEMO_DEEP_SOURCE,
            retrieval_path="memory_seed_deep",
            latency_ms=0,
            evidence_article_ids=item.get("evidence_article_ids", []),
            memory_reference=f"Deep seed: {item.get('outcome', '')}",
            created_at=created_at + timedelta(minutes=2),
        )
        db.add(advisory)
        inserted += 1
    return inserted


async def _seed_deep_ndvi(db: AsyncSession, field: Field, data: dict[str, Any]) -> int:
    points = data.get("extra_ndvi_weekly") or []
    existing = await db.scalar(
        select(func.count(SatelliteNDVI.id)).where(
            SatelliteNDVI.field_id == field.id,
            SatelliteNDVI.source == DEMO_DEEP_SOURCE,
        )
    )
    if existing:
        return 0
    for point in points:
        db.add(
            SatelliteNDVI(
                id=str(uuid.uuid4()),
                field_id=field.id,
                date=utc_now() - timedelta(weeks=int(point["weeks_ago"])),
                ndvi_value=float(point["ndvi"]),
                source=DEMO_DEEP_SOURCE,
                cloud_cover_pct=float(point["cloud_cover_pct"]),
            )
        )
    return len(points)


async def _seed_deep_finance(
    db: AsyncSession,
    farmer: Farmer,
    cycle: CropCycle,
    data: dict[str, Any],
) -> int:
    entries = data.get("extra_finance_entries") or []
    inserted = 0
    for entry in entries:
        dup_q = await db.execute(
            select(FinanceEntry.id).where(
                FinanceEntry.farmer_id == farmer.id,
                FinanceEntry.description == entry["description"],
            )
        )
        if dup_q.scalar_one_or_none():
            continue
        db.add(
            FinanceEntry(
                id=str(uuid.uuid4()),
                farmer_id=farmer.id,
                crop_cycle_id=cycle.id,
                entry_type=entry["type"],
                category=entry["category"],
                amount=float(entry["amount"]),
                description=entry["description"],
                recorded_at=utc_now() - timedelta(days=int(entry["days_ago"])),
            )
        )
        inserted += 1
    return inserted


async def _seed_extra_clusters(db: AsyncSession, data: dict[str, Any]) -> int:
    clusters = data.get("alert_clusters") or []
    inserted = 0
    for c in clusters:
        dup_q = await db.execute(
            select(AlertCluster.id).where(
                AlertCluster.district == c["district"],
                AlertCluster.tehsil == c.get("tehsil"),
                AlertCluster.village == c.get("village"),
                AlertCluster.crop_name == c["crop"],
                AlertCluster.issue_category == c["issue_category"],
            )
        )
        if dup_q.scalar_one_or_none():
            continue
        db.add(
            AlertCluster(
                id=str(uuid.uuid4()),
                district=c["district"],
                tehsil=c.get("tehsil"),
                village=c.get("village"),
                crop_name=c["crop"],
                issue_category=c["issue_category"],
                observation_ids=[],
                advisory_ids=[],
                farmer_count=int(c["farmer_count"]),
                severity=float(c["severity"]),
                status=AlertStatus.PENDING,
                broadcast_message=c["broadcast_message"],
            )
        )
        inserted += 1
    return inserted


# ─────────────────────────────────────────────────────────────────────────────
# /start graft — overwrite only the fields a fresh user provides; keep the
# rich 180-day history attached so the demo persona inherits Ram Kumar's
# memory under the new user's name.
# ─────────────────────────────────────────────────────────────────────────────

async def graft_demo_farmer(
    db: AsyncSession,
    *,
    telegram_user_id: str,
    partial_profile: dict[str, Any],
) -> dict[str, Any]:
    """Overwrite only the provided fields on the demo farmer + their first field.

    `partial_profile` may contain any of: name, village, tehsil, district,
    pincode, preferred_language, crop_name, area_acres, soil_type,
    irrigation_type. Unprovided keys are left untouched so the seeded
    180-day memory/NDVI/clusters keep making sense.

    If the demo farmer phone is not yet bound to this telegram_id, the row
    is re-keyed to telegram_user_id so all FK history transfers under the
    judge's identity.
    """
    farmer_q = await db.execute(
        select(Farmer).where(Farmer.phone.in_(["demo_farmer", telegram_user_id]))
    )
    farmer = farmer_q.scalars().first()
    if not farmer:
        return {"status": "no_demo_farmer"}

    if farmer.phone != telegram_user_id:
        farmer.phone = telegram_user_id

    changed: dict[str, Any] = {}
    for key, value in (partial_profile or {}).items():
        if value in (None, ""):
            continue
        if key in GRAFTABLE_FIELDS and hasattr(farmer, key):
            setattr(farmer, key, value)
            changed[f"farmer.{key}"] = value

    if any(k in (partial_profile or {}) for k in GRAFTABLE_FIELD_FIELDS):
        field_q = await db.execute(select(Field).where(Field.farmer_id == farmer.id))
        field = field_q.scalars().first()
        if field:
            for key in GRAFTABLE_FIELD_FIELDS:
                value = (partial_profile or {}).get(key)
                if value in (None, ""):
                    continue
                if key == "crop_name":
                    cycle_q = await db.execute(
                        select(CropCycle).where(
                            CropCycle.field_id == field.id,
                            CropCycle.is_active,
                        )
                    )
                    cycle = cycle_q.scalars().first()
                    if cycle:
                        cycle.crop_name = value
                        changed["cycle.crop_name"] = value
                elif hasattr(field, key):
                    setattr(field, key, value)
                    changed[f"field.{key}"] = value

    await db.commit()
    return {"status": "ok", "farmer_id": farmer.id, "changed": changed}
