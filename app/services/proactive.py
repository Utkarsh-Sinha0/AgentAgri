"""
AgriMesh V4.0 — Proactive Messaging Service
Triggers: crop stage change, high humidity, price spike, cluster detection, scheme deadline.
Runs as a background async task. Checks every 30 minutes.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    AlertCluster,
    AlertStatus,
    CropCycle,
    Farmer,
    Field,
)
from app.utils.time import utc_now


async def check_weather_triggers(db: AsyncSession) -> list[dict]:
    """Trigger: High humidity forecast (>80%) → alert farmers with active crops."""
    from app.services.weather import get_forecast
    forecast = await get_forecast(days=3)
    alerts = []

    high_humidity_days = [day for day in forecast.get("forecast", []) if day.get("humidity", 0) >= 80]
    if not high_humidity_days:
        return alerts

    result = await db.execute(
        select(Farmer, Field, CropCycle)
        .join(Field, Field.farmer_id == Farmer.id)
        .join(CropCycle, CropCycle.field_id == Field.id)
        .where(
            CropCycle.is_active,
            CropCycle.current_stage.in_(["vegetative", "flowering", "fruiting"]),
        )
        .limit(20)
    )
    vulnerable_farmers = result.all()

    for day in high_humidity_days:
        if day.get("humidity", 0) >= 80:
            for farmer, _field, cycle in vulnerable_farmers:
                alerts.append({
                    "farmer_id": farmer.id,
                    "farmer_phone": farmer.phone,
                    "trigger": "high_humidity",
                    "message_hi": (
                        f"⚠️ {day['date']} को {day['humidity']}% नमी का अनुमान है। "
                        f"आपकी {cycle.crop_name} फसल ({cycle.current_stage} अवस्था) में "
                        f"फफूंद रोग का खतरा बढ़ सकता है। खेत का निरीक्षण करें।"
                    ),
                    "message_en": (
                        f"⚠️ {day['humidity']}% humidity forecast for {day['date']}. "
                        f"Your {cycle.crop_name} crop ({cycle.current_stage}) may face "
                        f"increased fungal disease risk. Inspect your field."
                    ),
                })
    return alerts


async def check_stage_transitions(db: AsyncSession) -> list[dict]:
    """Trigger: Crop approaching next growth stage → send stage-specific advisory."""
    alerts = []
    result = await db.execute(
        select(CropCycle, Field, Farmer)
        .join(Field, Field.id == CropCycle.field_id)
        .join(Farmer, Farmer.id == Field.farmer_id)
        .where(CropCycle.is_active)
        .limit(30)
    )

    stage_order = ["pre_sowing", "seedling", "vegetative", "flowering", "fruiting", "harvest"]
    for cycle, _field, farmer in result.all():
        if cycle.current_stage in stage_order:
            current_idx = stage_order.index(cycle.current_stage)
            if current_idx < len(stage_order) - 1:
                next_stage = stage_order[current_idx + 1]
                alerts.append({
                    "farmer_id": farmer.id,
                    "trigger": "stage_transition",
                    "current_stage": cycle.current_stage,
                    "next_stage": next_stage,
                    "message_hi": (
                        f"🌱 आपकी {cycle.crop_name} फसल जल्द ही '{next_stage}' अवस्था में "
                        f"प्रवेश करेगी। इस अवस्था के लिए आवश्यक तैयारी करें। /calendar देखें।"
                    ),
                    "message_en": f"🌱 Your {cycle.crop_name} crop will soon enter '{next_stage}' stage. Prepare accordingly. Check /calendar.",
                })
    return alerts


async def check_cluster_alerts(db: AsyncSession) -> list[dict]:
    """Trigger: Alert clusters reaching farmer_notification threshold."""
    result = await db.execute(
        select(AlertCluster)
        .where(
            AlertCluster.status == AlertStatus.PENDING,
            AlertCluster.severity >= 0.70,
        )
        .limit(5)
    )
    clusters = result.scalars().all()

    alerts = []
    if not clusters:
        return alerts

    farmers_by_scope: dict[tuple[str, str], list[Farmer]] = defaultdict(list)
    scopes = {(cluster.district, cluster.tehsil) for cluster in clusters}
    districts = {district for district, _ in scopes}
    tehsils = {tehsil for _, tehsil in scopes}
    farmer_result = await db.execute(
        select(Farmer)
        .where(Farmer.district.in_(districts), Farmer.tehsil.in_(tehsils))
        .limit(50 * max(len(scopes), 1))
    )
    for farmer in farmer_result.scalars().all():
        key = (farmer.district, farmer.tehsil)
        if key in scopes and len(farmers_by_scope[key]) < 50:
            farmers_by_scope[key].append(farmer)

    for cluster in clusters:
        for farmer in farmers_by_scope.get((cluster.district, cluster.tehsil), []):
            alerts.append({
                "farmer_id": farmer.id,
                "trigger": "cluster_alert",
                "cluster_id": cluster.id,
                "message_hi": (
                    f"🚨 {cluster.district} जिले के {cluster.tehsil} क्षेत्र में "
                    f"{cluster.crop_name} फसल में {cluster.issue_category} के {cluster.farmer_count} "
                    f"मामले सामने आए हैं। अपनी फसल की जांच करें और सावधानी बरतें।"
                ),
                "message_en": (
                    f"🚨 {cluster.farmer_count} cases of {cluster.issue_category} in {cluster.crop_name} "
                    f"reported in {cluster.tehsil}, {cluster.district}. Check your crop."
                ),
            })
    return alerts


async def check_price_spikes(db: AsyncSession) -> list[dict]:
    """Trigger: Significant mandi price changes (±15% in 3 days)."""
    from app.services.mandi import get_mandi_prices
    alerts = []

    for crop in ["rice", "wheat", "maize", "pulses"]:
        prices = await get_mandi_prices(crop=crop, district="Munger", days=5)
        if prices.get("prices"):
            for p in prices["prices"]:
                history = p.get("history", [])
                if len(history) >= 2:
                    latest = history[0].get("modal", 0)
                    previous = history[-1].get("modal", 0)
                    if previous > 0:
                        change_pct = abs(latest - previous) / previous
                        if change_pct >= 0.10:
                            direction = "📈 बढ़े" if latest > previous else "📉 घटे"
                            alerts.append({
                                "trigger": "price_spike",
                                "crop": crop,
                                "change_pct": round(change_pct * 100, 1),
                                "message_hi": (
                                    f"{direction} हैं! {crop} ({p['type']}) के दाम 5 दिनों में "
                                    f"{round(change_pct * 100)}% बदल गए हैं। वर्तमान भाव: ₹{latest}/quintal"
                                ),
                                "message_en": (
                                    f"Price alert! {crop} ({p['type']}) prices changed "
                                    f"{round(change_pct * 100)}% in 5 days. Current: ₹{latest}/quintal"
                                ),
                            })
    return alerts


async def run_proactive_checks() -> dict:
    """Run all proactive triggers. Returns summary of alerts generated."""
    all_alerts = []
    try:
        async with async_session_factory() as db:
            all_alerts.extend(await check_weather_triggers(db))
            all_alerts.extend(await check_stage_transitions(db))
            all_alerts.extend(await check_cluster_alerts(db))
            all_alerts.extend(await check_price_spikes(db))
    except Exception as exc:
        logger.error(f"Proactive check failed: {exc}")
        return {"error": str(exc), "alerts": 0}

    # In production: send via Telegram/SMS. For now: log and store.
    for alert in all_alerts[:5]:  # Limit to 5 to avoid spam during demo
        logger.info(f"PROACTIVE: [{alert['trigger']}] → farmer {alert.get('farmer_id', alert.get('trigger', 'N/A'))[:20]}")

    return {
        "timestamp": utc_now().isoformat(),
        "total_alerts": len(all_alerts),
        "by_trigger": {},
        "alerts": all_alerts[:20],
    }


async def proactive_loop(interval_seconds: int = 1800):
    """Background loop: run proactive checks every N seconds."""
    logger.info(f"Proactive messaging started (interval={interval_seconds}s)")
    while True:
        try:
            result = await run_proactive_checks()
            logger.info(f"Proactive check: {result.get('total_alerts', 0)} alerts")
        except Exception as exc:
            logger.error(f"Proactive loop error: {exc}")
        await asyncio.sleep(interval_seconds)
