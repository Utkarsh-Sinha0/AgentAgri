"""
AgriMesh V4.0 — Alert System with Similarity Scoring (§14)
6-factor similarity: crop(20%) + stage(15%) + symptoms(20%) + time(15%) + distance(15%) + weather(15%)
Full lifecycle: INTERNAL_WATCH → FARMER_WATCH → EXTENSION_REVIEW → APPROVED/REJECTED → EXPIRED
"""
from __future__ import annotations

from datetime import timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Advisory,
    AlertCluster,
    AlertStatus,
    CropCycle,
    Farmer,
    Observation,
)
from app.utils.time import utc_now

# ─── Similarity Scoring (§14.1) ───────────────────────────────────────

def compute_similarity(
    obs_a: dict,
    obs_b: dict,
    weights: dict | None = None,
) -> float:
    """
    Compute similarity between two observations.
    Weights: crop(0.20), stage(0.15), symptoms(0.20), time(0.15), distance(0.15), weather(0.15)
    """
    w = weights or {
        "crop": 0.20, "stage": 0.15, "symptoms": 0.20,
        "time": 0.15, "distance": 0.15, "weather": 0.15,
    }
    score = 0.0

    # Crop match
    if obs_a.get("crop_name") == obs_b.get("crop_name"):
        score += w["crop"]

    # Growth stage match
    if obs_a.get("stage") == obs_b.get("stage"):
        score += w["stage"]

    # Symptom tag overlap
    tags_a = set(obs_a.get("symptom_tags", []))
    tags_b = set(obs_b.get("symptom_tags", []))
    if tags_a and tags_b:
        overlap = len(tags_a & tags_b) / max(len(tags_a | tags_b), 1)
        score += w["symptoms"] * overlap

    # Time proximity (within 72 hours = full score, linear decay to 7 days)
    time_a = obs_a.get("created_at")
    time_b = obs_b.get("created_at")
    if time_a and time_b:
        diff_hours = abs((time_a - time_b).total_seconds()) / 3600
        if diff_hours <= 72:
            score += w["time"]
        elif diff_hours <= 168:
            score += w["time"] * (1 - (diff_hours - 72) / 96)

    # Distance proximity (within 15km = full, linear decay to 50km)
    dist = obs_a.get("_distance_km", 999)
    if dist <= 15:
        score += w["distance"]
    elif dist <= 50:
        score += w["distance"] * (1 - (dist - 15) / 35)

    # Weather similarity (simplified: same humidity category)
    if obs_a.get("weather_humidity_category") == obs_b.get("weather_humidity_category"):
        score += w["weather"]

    return round(min(score, 1.0), 3)


def _extract_symptom_tags(text: str | None) -> list[str]:
    """Extract symptom-related keywords from observation text."""
    if not text:
        return []
    tags = set()
    keyword_map = {
        "spots": ["धब्बा", "spot", "blotch"],
        "yellow": ["पीला", "yellow", "पीलापन"],
        "wilt": ["मुरझा", "wilt", "सूख"],
        "insect": ["कीड़ा", "insect", "कीट", "सूंडी"],
        "fungus": ["फफूंद", "fungus", "फंगस", "सफेद पाउडर"],
        "rot": ["सड़", "rot", "gal"],
        "water": ["पानी", "water", "जलभराव", "बारिश"],
    }
    tl = text.lower()
    for tag, keywords in keyword_map.items():
        if any(kw in tl for kw in keywords):
            tags.add(tag)
    return list(tags)


# ─── Cluster Lifecycle (§14.3) ────────────────────────────────────────

async def find_or_create_cluster(
    db: AsyncSession,
    observation: Observation,
    advisory: Advisory | None = None,
) -> AlertCluster | None:
    """
    After each observation: check similarity with recent observations,
    create or update alert clusters.
    """
    cutoff = utc_now() - timedelta(hours=72)

    # Get farmer info
    farmer_result = await db.execute(
        select(Farmer).where(Farmer.id == observation.farmer_id)
    )
    farmer = farmer_result.scalar_one_or_none()
    if not farmer:
        return None

    # H16: resolve the current observation's crop from CropCycle (not from
    # advisory.actions_text which holds action sentences, not crop names).
    current_crop_name: str | None = None
    if observation.crop_cycle_id:
        cc_result = await db.execute(
            select(CropCycle.crop_name).where(CropCycle.id == observation.crop_cycle_id)
        )
        current_crop_name = cc_result.scalar_one_or_none()

    # Get recent observations in same district
    recent = await db.execute(
        select(Observation, Farmer, CropCycle)
        .join(Farmer, Farmer.id == Observation.farmer_id)
        .join(CropCycle, CropCycle.id == Observation.crop_cycle_id, isouter=True)
        .where(
            Observation.created_at >= cutoff,
            Observation.id != observation.id,
            Farmer.district == farmer.district,
        )
        .limit(50)
    )
    rows = recent.all()

    if len(rows) < 2:
        return None  # Need at least 3 total (including current)

    # Build observation dicts for scoring
    current_obs_dict = {
        "crop_name": current_crop_name,
        "stage": observation.reported_stage,
        "symptom_tags": _extract_symptom_tags(observation.text_content),
        "created_at": observation.created_at,
    }

    similar = []
    for other_obs, _other_farmer, other_cycle in rows:
        other_dict = {
            "crop_name": other_cycle.crop_name if other_cycle else None,
            "stage": other_obs.reported_stage,
            "symptom_tags": _extract_symptom_tags(other_obs.text_content),
            "created_at": other_obs.created_at,
        }
        sim = compute_similarity(current_obs_dict, other_dict)
        if sim >= 0.30:
            similar.append((other_obs, sim))

    if len(similar) < 2:
        return None

    # Create or update cluster
    import uuid
    cluster = AlertCluster(
        id=str(uuid.uuid4()),
        district=farmer.district,
        tehsil=farmer.tehsil or "",
        village=farmer.village,
        crop_name=current_crop_name or "unknown",
        issue_category=advisory.risk_level if advisory else "UNKNOWN",
        observation_ids=[observation.id] + [o.id for o, _ in similar],
        advisory_ids=[advisory.id] if advisory else [],
        farmer_count=1 + len(similar),
        severity=min(0.40 + (len(similar) * 0.05), 0.95),
        status=AlertStatus.PENDING,
    )
    db.add(cluster)
    await db.commit()

    logger.info(
        f"Alert cluster created: {cluster.district}/{cluster.tehsil} "
        f"— {cluster.farmer_count} farmers, severity={cluster.severity:.2f}"
    )
    return cluster


async def transition_cluster(
    db: AsyncSession,
    cluster_id: str,
    new_status: str,
    by_worker_id: str | None = None,
) -> dict:
    """Transition a cluster through its lifecycle states."""
    result = await db.execute(
        select(AlertCluster).where(AlertCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        return {"error": "Cluster not found"}

    valid_transitions = {
        AlertStatus.PENDING: [AlertStatus.REVIEWED, AlertStatus.DISMISSED],
        AlertStatus.REVIEWED: [AlertStatus.BROADCAST, AlertStatus.DISMISSED],
    }

    current = cluster.status
    if new_status not in valid_transitions.get(current, []):
        return {
            "error": f"Invalid transition: {current} → {new_status}",
            "valid_transitions": valid_transitions.get(current, []),
        }

    cluster.status = new_status
    if by_worker_id:
        cluster.reviewed_by = by_worker_id
    if new_status == AlertStatus.BROADCAST:
        cluster.broadcast_at = utc_now()
        cluster.farmers_notified = cluster.farmer_count

    await db.commit()
    return {"cluster_id": cluster_id, "old_status": current, "new_status": new_status}
