"""
Farmer-facing dashboard aggregation.
Builds a single low-latency payload for the PWA from existing local-first data:
profile, fields, crops, weather, NDVI, finance, advisories, conversations, and clusters.
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Advisory,
    AlertCluster,
    CropCalendarTask,
    CropCycle,
    Farmer,
    Field,
    FinanceEntry,
    SatelliteNDVI,
)
from app.models_memory import (
    ActionImpact,
    ConversationThread,
    ConversationTurn,
    FarmerProfile,
    MemoryAtom,
)
from app.services.mandi import get_mandi_prices
from app.services.weather import get_forecast, get_historical_weather


async def get_farmer_dashboard(
    db: AsyncSession,
    farmer_id: str | None = None,
    phone: str | None = None,
) -> dict[str, Any]:
    farmer = await _resolve_farmer(db, farmer_id=farmer_id, phone=phone)
    if not farmer:
        return {"error": "farmer_not_found"}

    profile = await db.scalar(select(FarmerProfile).where(FarmerProfile.farmer_id == farmer.id))
    fields = await _field_cards(db, farmer)
    active_field = fields[0] if fields else None
    active_crop = active_field["active_crop"] if active_field else None
    crop_name = active_crop["crop_name"] if active_crop else "rice"

    weather = await get_forecast(active_field["id"] if active_field else None, days=5)
    historical_weather = await get_historical_weather(active_field["id"] if active_field else None, days=7)
    mandi = await get_mandi_prices(crop_name, district=farmer.district or "Munger", days=7)
    advisories = await _latest_advisories(db, farmer.id)
    conversations = await _conversation_summary(db, farmer.id)
    impacts = await _latest_impacts(db, farmer.id)
    clusters = await _cluster_map(db, farmer, active_field)
    finance = await _finance_summary(db, farmer.id)
    memory = await _memory_summary(db, farmer.id)
    onboarding = _profile_questions(profile)
    weather_skin = _weather_skin(weather)

    return {
        "farmer": {
            "id": farmer.id,
            "name": farmer.name,
            "phone": farmer.phone,
            "preferred_language": farmer.preferred_language,
            "district": farmer.district,
            "tehsil": farmer.tehsil,
            "village": farmer.village,
        },
        "profile": _serialize_profile(profile),
        "profile_questions": onboarding,
        "fields": fields,
        "weather": weather,
        "historical_weather": historical_weather,
        "weather_skin": weather_skin,
        "mandi": mandi,
        "finance": finance,
        "advisories": advisories,
        "conversations": conversations,
        "impacts": impacts,
        "clusters": clusters,
        "memory": memory,
        "sync": {
            "server_generated_at": datetime.utcnow().isoformat(),
            "local_cache_key": f"agrimesh_farmer_dashboard_{farmer.id}",
            "offline_ready": True,
        },
    }


async def upsert_farmer_profile(
    db: AsyncSession,
    farmer_id: str,
    payload: dict[str, Any],
) -> dict[str, Any]:
    farmer = await db.scalar(select(Farmer).where(Farmer.id == farmer_id))
    if not farmer:
        return {"error": "farmer_not_found"}

    profile = await db.scalar(select(FarmerProfile).where(FarmerProfile.farmer_id == farmer_id))
    if not profile:
        profile = FarmerProfile(farmer_id=farmer_id)
        db.add(profile)
        await db.flush()

    scalar_fields = {
        "farm_size_acres",
        "irrigation_source",
        "water_reliability",
        "soil_test_status",
        "primary_soil_type",
        "labor_availability",
        "storage_access",
        "transport_access",
        "annual_budget_rs",
        "risk_tolerance",
        "credit_access",
        "insurance_status",
        "organic_preference",
        "nearest_mandi_km",
        "pm_kisan_enrolled",
        "pmfby_enrolled",
        "kcc_holder",
        "soil_health_card",
    }
    list_fields = {"equipment_access", "preferred_mandis"}

    for key in scalar_fields:
        if key in payload:
            setattr(profile, key, payload[key])
    for key in list_fields:
        if key in payload:
            value = payload[key]
            if isinstance(value, str):
                value = [item.strip() for item in value.split(",") if item.strip()]
            setattr(profile, key, value or [])

    profile.profile_completeness = _profile_completeness(profile)
    profile.last_updated = datetime.utcnow()
    await db.commit()
    return {"profile": _serialize_profile(profile)}


async def _resolve_farmer(
    db: AsyncSession,
    farmer_id: str | None,
    phone: str | None,
) -> Farmer | None:
    if farmer_id:
        farmer = await db.scalar(select(Farmer).where(Farmer.id == farmer_id))
        if farmer:
            return farmer
    if phone:
        farmer = await db.scalar(select(Farmer).where(Farmer.phone == phone))
        if farmer:
            return farmer
    return await db.scalar(select(Farmer).order_by(Farmer.registration_date).limit(1))


async def _field_cards(db: AsyncSession, farmer: Farmer) -> list[dict[str, Any]]:
    result = await db.execute(select(Field).where(Field.farmer_id == farmer.id).order_by(Field.created_at))
    fields = result.scalars().all()
    cards = []
    for field in fields:
        cycle = await db.scalar(
            select(CropCycle)
            .where(CropCycle.field_id == field.id, CropCycle.is_active)
            .order_by(desc(CropCycle.created_at))
            .limit(1)
        )
        ndvi_result = await db.execute(
            select(SatelliteNDVI)
            .where(SatelliteNDVI.field_id == field.id)
            .order_by(SatelliteNDVI.date)
            .limit(16)
        )
        ndvi = list(ndvi_result.scalars().all())
        tasks = []
        if cycle:
            task_result = await db.execute(
                select(CropCalendarTask)
                .where(CropCalendarTask.cycle_id == cycle.id)
                .order_by(CropCalendarTask.days_from_sowing)
                .limit(8)
            )
            tasks = [_serialize_task(task) for task in task_result.scalars().all()]

        cards.append(
            {
                "id": field.id,
                "name": field.name,
                "area_acres": field.area_acres,
                "soil_type": field.soil_type,
                "soil_ph": field.soil_ph,
                "irrigation_type": field.irrigation_type,
                "lat": field.lat,
                "lng": field.lng,
                "active_crop": _serialize_cycle(cycle) if cycle else None,
                "ndvi": [_serialize_ndvi(item) for item in ndvi],
                "ndvi_trend": _ndvi_trend(ndvi),
                "tasks": tasks,
            }
        )
    return cards


async def _latest_advisories(db: AsyncSession, farmer_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        select(Advisory)
        .where(Advisory.farmer_id == farmer_id)
        .order_by(desc(Advisory.created_at))
        .limit(8)
    )
    return [
        {
            "id": item.id,
            "risk_level": item.risk_level,
            "confidence": item.confidence,
            "contextualization": item.contextualization,
            "actions_text": item.actions_text or [],
            "warnings_text": item.warnings_text or [],
            "memory_reference": item.memory_reference,
            "created_at": item.created_at.isoformat() if item.created_at else None,
        }
        for item in result.scalars().all()
    ]


async def _conversation_summary(db: AsyncSession, farmer_id: str) -> list[dict[str, Any]]:
    threads_result = await db.execute(
        select(ConversationThread)
        .where(ConversationThread.farmer_id == farmer_id)
        .order_by(desc(ConversationThread.updated_at))
        .limit(6)
    )
    rows = []
    for thread in threads_result.scalars().all():
        turns_result = await db.execute(
            select(ConversationTurn)
            .where(ConversationTurn.thread_id == thread.id)
            .order_by(desc(ConversationTurn.created_at))
            .limit(3)
        )
        rows.append(
            {
                "id": thread.id,
                "field_id": thread.field_id,
                "crop_cycle_id": thread.crop_cycle_id,
                "title": thread.title,
                "running_summary": thread.running_summary,
                "turn_count": thread.turn_count,
                "last_advisory_id": thread.last_advisory_id,
                "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
                "recent_turns": [
                    {
                        "user_message": turn.user_message,
                        "agent_response": turn.agent_response,
                        "risk_level": turn.risk_level,
                        "created_at": turn.created_at.isoformat() if turn.created_at else None,
                    }
                    for turn in turns_result.scalars().all()
                ],
            }
        )
    return rows


async def _latest_impacts(db: AsyncSession, farmer_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        select(ActionImpact)
        .where(ActionImpact.farmer_id == farmer_id)
        .order_by(desc(ActionImpact.created_at))
        .limit(10)
    )
    return [
        {
            "id": item.id,
            "advisory_id": item.advisory_id,
            "action_text": item.action_text,
            "impact_level": item.impact_level,
            "expected_result": item.expected_result,
            "time_horizon": item.time_horizon,
            "dependencies": item.dependencies or [],
            "risks": item.risks or [],
            "metrics_delta": item.metrics_delta or {},
        }
        for item in result.scalars().all()
    ]


async def _cluster_map(
    db: AsyncSession,
    farmer: Farmer,
    active_field: dict[str, Any] | None,
) -> dict[str, Any]:
    result = await db.execute(
        select(AlertCluster)
        .where(AlertCluster.district == farmer.district)
        .order_by(desc(AlertCluster.severity))
        .limit(50)
    )
    base_lat = active_field.get("lat") if active_field else None
    base_lng = active_field.get("lng") if active_field else None
    base_lat = base_lat or 25.38
    base_lng = base_lng or 86.47
    clusters = []
    for idx, cluster in enumerate(result.scalars().all()):
        lat, lng = _cluster_position(base_lat, base_lng, cluster.id, idx)
        clusters.append(
            {
                "id": cluster.id,
                "lat": lat,
                "lng": lng,
                "district": cluster.district,
                "tehsil": cluster.tehsil,
                "village": cluster.village,
                "crop_name": cluster.crop_name,
                "issue_category": cluster.issue_category,
                "farmer_count": cluster.farmer_count,
                "severity": cluster.severity,
                "status": cluster.status,
                "broadcast_message": cluster.broadcast_message,
                "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
                "merge_keys": {
                    "field": cluster.id,
                    "village": f"{cluster.district}:{cluster.tehsil}:{cluster.village or 'unknown'}",
                    "tehsil": f"{cluster.district}:{cluster.tehsil}",
                    "district": cluster.district or "unknown",
                    "state": "Bihar",
                },
            }
        )
    return {
        "center": {"lat": base_lat, "lng": base_lng},
        "own_field": active_field,
        "clusters": clusters,
        "zoom_levels": [
            {"level": "field", "min_zoom": 14},
            {"level": "village", "min_zoom": 11},
            {"level": "tehsil", "min_zoom": 8},
            {"level": "district", "min_zoom": 5},
            {"level": "state", "min_zoom": 0},
        ],
    }


async def _finance_summary(db: AsyncSession, farmer_id: str) -> dict[str, Any]:
    result = await db.execute(select(FinanceEntry).where(FinanceEntry.farmer_id == farmer_id))
    rows = list(result.scalars().all())
    expenses = sum(item.amount for item in rows if item.entry_type == "expense")
    revenue = sum(item.amount for item in rows if item.entry_type == "revenue")
    by_category: dict[str, float] = {}
    for item in rows:
        sign = -1 if item.entry_type == "expense" else 1
        by_category[item.category or "other"] = by_category.get(item.category or "other", 0) + sign * item.amount
    return {
        "expenses": round(expenses, 2),
        "revenue": round(revenue, 2),
        "net": round(revenue - expenses, 2),
        "entries": len(rows),
        "by_category": by_category,
    }


async def _memory_summary(db: AsyncSession, farmer_id: str) -> dict[str, Any]:
    atom_count = await db.scalar(select(func.count(MemoryAtom.id)).where(MemoryAtom.farmer_id == farmer_id))
    result = await db.execute(
        select(MemoryAtom)
        .where(MemoryAtom.farmer_id == farmer_id)
        .order_by(desc(MemoryAtom.event_at))
        .limit(8)
    )
    return {
        "atom_count": atom_count or 0,
        "recent": [
            {
                "type": item.atom_type,
                "summary": item.summary,
                "confidence": item.confidence,
                "event_at": item.event_at.isoformat() if item.event_at else None,
            }
            for item in result.scalars().all()
        ],
    }


def _serialize_profile(profile: FarmerProfile | None) -> dict[str, Any]:
    if not profile:
        return {"profile_completeness": 0.0}
    fields = [
        "farm_size_acres",
        "irrigation_source",
        "water_reliability",
        "soil_test_status",
        "primary_soil_type",
        "equipment_access",
        "labor_availability",
        "storage_access",
        "transport_access",
        "annual_budget_rs",
        "risk_tolerance",
        "credit_access",
        "insurance_status",
        "organic_preference",
        "preferred_mandis",
        "nearest_mandi_km",
        "pm_kisan_enrolled",
        "pmfby_enrolled",
        "kcc_holder",
        "soil_health_card",
        "profile_completeness",
    ]
    data = {field: getattr(profile, field) for field in fields}
    data.update(
        {
            "id": profile.id,
            "farmer_id": profile.farmer_id,
            "last_updated": profile.last_updated.isoformat() if profile.last_updated else None,
        }
    )
    return data


def _profile_questions(profile: FarmerProfile | None) -> list[dict[str, Any]]:
    current = _serialize_profile(profile)
    questions = [
        ("farm_size_acres", "Total farm size", "number", "How many acres do you cultivate in total?"),
        ("irrigation_source", "Irrigation source", "select", "What is your main water source?"),
        ("water_reliability", "Water reliability", "select", "How reliable is water during the season?"),
        ("soil_test_status", "Soil test", "select", "When was your last soil test?"),
        ("annual_budget_rs", "Season budget", "number", "Approximate crop budget for this season in rupees?"),
        ("risk_tolerance", "Risk comfort", "select", "Do you prefer lowest-risk or higher-return advice?"),
        ("preferred_mandis", "Preferred mandis", "text", "Which mandis do you usually sell at?"),
        ("equipment_access", "Equipment access", "text", "Which tools do you have access to?"),
        ("credit_access", "Credit access", "select", "What credit source can you use if needed?"),
        ("insurance_status", "Insurance", "select", "Are you enrolled in crop insurance?"),
    ]
    options = {
        "irrigation_source": ["canal", "borewell", "rainfed", "drip", "pond"],
        "water_reliability": ["reliable", "seasonal", "unreliable"],
        "soil_test_status": ["done_recently", "done_old", "never", "in_progress"],
        "risk_tolerance": ["low", "medium", "high"],
        "credit_access": ["kcc", "bank_loan", "moneylender", "none"],
        "insurance_status": ["pmfby_enrolled", "other", "none"],
    }
    return [
        {
            "id": key,
            "label": label,
            "input_type": input_type,
            "question": question,
            "value": current.get(key),
            "options": options.get(key, []),
            "is_missing": current.get(key) in (None, "", [], 0),
        }
        for key, label, input_type, question in questions
    ]


def _profile_completeness(profile: FarmerProfile) -> float:
    keys = [
        "farm_size_acres",
        "irrigation_source",
        "water_reliability",
        "soil_test_status",
        "primary_soil_type",
        "annual_budget_rs",
        "risk_tolerance",
        "credit_access",
        "insurance_status",
        "preferred_mandis",
    ]
    filled = 0
    for key in keys:
        value = getattr(profile, key)
        if value not in (None, "", [], 0):
            filled += 1
    return round(filled / len(keys), 2)


def _serialize_cycle(cycle: CropCycle | None) -> dict[str, Any] | None:
    if not cycle:
        return None
    days_after_sowing = None
    if cycle.sowing_date:
        days_after_sowing = max(0, (datetime.utcnow() - cycle.sowing_date).days)
    return {
        "id": cycle.id,
        "crop_name": cycle.crop_name,
        "variety": cycle.variety,
        "current_stage": cycle.current_stage,
        "sowing_date": cycle.sowing_date.isoformat() if cycle.sowing_date else None,
        "expected_harvest_date": cycle.expected_harvest_date.isoformat() if cycle.expected_harvest_date else None,
        "days_after_sowing": days_after_sowing,
    }


def _serialize_task(task: CropCalendarTask) -> dict[str, Any]:
    return {
        "id": task.id,
        "stage": task.stage,
        "task_name": task.task_name,
        "description": task.description,
        "days_from_sowing": task.days_from_sowing,
        "completed": task.completed,
    }


def _serialize_ndvi(item: SatelliteNDVI) -> dict[str, Any]:
    return {
        "date": item.date.isoformat() if item.date else None,
        "ndvi": item.ndvi_value,
        "source": item.source,
        "cloud_cover_pct": item.cloud_cover_pct,
    }


def _ndvi_trend(rows: list[SatelliteNDVI]) -> dict[str, Any]:
    if len(rows) < 2:
        return {"status": "unknown", "change": 0}
    change = rows[-1].ndvi_value - rows[0].ndvi_value
    status = "improving" if change > 0.04 else "declining" if change < -0.04 else "stable"
    return {"status": status, "change": round(change, 3), "latest": rows[-1].ndvi_value}


def _weather_skin(weather: dict[str, Any]) -> dict[str, Any]:
    forecast = weather.get("forecast", [])
    current = forecast[0] if forecast else {}
    condition = current.get("condition", "sunny")
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    is_night = ist_now.hour < 6 or ist_now.hour >= 18
    rainfall = float(current.get("rainfall_mm") or 0)
    humidity = float(current.get("humidity") or 0)
    if rainfall >= 10:
        mood = "storm" if is_night else "rain"
    elif "cloud" in condition or humidity >= 80:
        mood = "cloudy_night" if is_night else "cloudy"
    else:
        mood = "clear_night" if is_night else "sunny"
    return {
        "mood": mood,
        "is_night": is_night,
        "condition": condition,
        "rainfall_mm": rainfall,
        "humidity": humidity,
        "local_time": ist_now.isoformat(),
    }


def _cluster_position(base_lat: float, base_lng: float, cluster_id: str, idx: int) -> tuple[float, float]:
    seed = sum(ord(ch) for ch in cluster_id)
    angle = ((seed % 360) / 180) * math.pi
    radius = 0.012 + (idx % 6) * 0.008
    return round(base_lat + math.sin(angle) * radius, 5), round(base_lng + math.cos(angle) * radius, 5)
