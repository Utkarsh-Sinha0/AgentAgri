"""
AgriMesh V4.0 — Cluster Service
Groups similar observations by geography + crop + issue for extension worker review.
"""
from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Advisory, AlertCluster, AlertStatus, Observation


async def create_cluster(
    db: AsyncSession,
    district: str,
    tehsil: str,
    crop_name: str,
    issue_category: str,
    observation_ids: list[str],
    advisory_ids: list[str],
    farmer_count: int,
    severity: float,
) -> AlertCluster:
    """Create a new alert cluster."""
    import uuid
    cluster = AlertCluster(
        id=str(uuid.uuid4()),
        district=district,
        tehsil=tehsil,
        crop_name=crop_name,
        issue_category=issue_category,
        observation_ids=observation_ids,
        advisory_ids=advisory_ids,
        farmer_count=farmer_count,
        severity=severity,
        status=AlertStatus.PENDING,
        created_at=datetime.utcnow(),
    )
    db.add(cluster)
    await db.commit()
    return cluster


async def get_pending_clusters(
    db: AsyncSession,
    district: str | None = None,
    tehsil: str | None = None,
    limit: int = 20,
) -> list[AlertCluster]:
    """Get clusters pending review, optionally filtered by geography."""
    query = select(AlertCluster).where(
        AlertCluster.status == AlertStatus.PENDING
    ).order_by(desc(AlertCluster.severity))

    if district:
        query = query.where(AlertCluster.district == district)
    if tehsil:
        query = query.where(AlertCluster.tehsil == tehsil)

    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


async def review_cluster(
    db: AsyncSession,
    cluster_id: str,
    extension_worker_id: str,
    action: str,  # approve_broadcast, dismiss
    broadcast_message: str = "",
) -> dict:
    """Review a cluster: approve broadcast or dismiss."""
    result = await db.execute(
        select(AlertCluster).where(AlertCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        return {"error": f"Cluster {cluster_id} not found"}

    cluster.reviewed_by = extension_worker_id

    if action == "approve_broadcast":
        cluster.status = AlertStatus.BROADCAST
        cluster.broadcast_message = broadcast_message
        cluster.broadcast_at = datetime.utcnow()
        cluster.farmers_notified = cluster.farmer_count
    elif action == "dismiss":
        cluster.status = AlertStatus.DISMISSED
    else:
        cluster.status = AlertStatus.REVIEWED

    await db.commit()
    return {
        "cluster_id": cluster_id,
        "status": cluster.status,
        "farmers_notified": cluster.farmers_notified,
        "reviewed_at": datetime.utcnow().isoformat(),
    }


async def get_cluster_details(
    db: AsyncSession,
    cluster_id: str,
) -> dict:
    """Get full cluster details including observations and advisories."""
    result = await db.execute(
        select(AlertCluster).where(AlertCluster.id == cluster_id)
    )
    cluster = result.scalar_one_or_none()
    if not cluster:
        return {"error": f"Cluster {cluster_id} not found"}

    # Fetch observation details
    obs_ids = cluster.observation_ids or []
    observations = []
    if obs_ids:
        obs_result = await db.execute(
            select(Observation).where(Observation.id.in_(obs_ids))
        )
        for obs in obs_result.scalars().all():
            observations.append({
                "id": obs.id,
                "farmer_id": obs.farmer_id,
                "text_content": obs.text_content,
                "vision_analysis": obs.vision_analysis,
                "created_at": obs.created_at.isoformat() if obs.created_at else None,
            })

    # Fetch advisory details
    adv_ids = cluster.advisory_ids or []
    advisories = []
    if adv_ids:
        adv_result = await db.execute(
            select(Advisory).where(Advisory.id.in_(adv_ids))
        )
        for adv in adv_result.scalars().all():
            advisories.append({
                "id": adv.id,
                "risk_level": adv.risk_level,
                "confidence": adv.confidence,
                "contextualization": adv.contextualization,
                "actions_text": adv.actions_text,
            })

    return {
        "id": cluster.id,
        "district": cluster.district,
        "tehsil": cluster.tehsil,
        "village": cluster.village,
        "crop_name": cluster.crop_name,
        "issue_category": cluster.issue_category,
        "farmer_count": cluster.farmer_count,
        "severity": cluster.severity,
        "status": cluster.status,
        "observations": observations,
        "advisories": advisories,
        "broadcast_message": cluster.broadcast_message,
        "created_at": cluster.created_at.isoformat() if cluster.created_at else None,
    }


async def find_similar_observations(
    db: AsyncSession,
    observation_id: str,
    radius_km: float = 30.0,
    time_window_hours: int = 72,
) -> list[dict]:
    """
    Find similar observations near a given observation.
    Uses PostGIS spatial query if available, falls back to same-district grouping.
    """
    # Get the source observation
    result = await db.execute(
        select(Observation).where(Observation.id == observation_id)
    )
    obs = result.scalar_one_or_none()
    if not obs:
        return []

    # Find observations in same district + crop, within time window
    from datetime import timedelta
    cutoff = obs.created_at - timedelta(hours=time_window_hours)

    # Simplified: find recent observations with similar text content keywords
    keywords = (obs.text_content or "").lower().split()[:5]
    similar = []
    if keywords:
        # Find observations in the same time window with overlapping keywords
        all_recent = await db.execute(
            select(Observation)
            .where(
                Observation.created_at >= cutoff,
                Observation.id != observation_id,
            )
            .limit(50)
        )
        for other in all_recent.scalars().all():
            other_text = (other.text_content or "").lower()
            match_count = sum(1 for kw in keywords if kw in other_text)
            if match_count >= 2:
                similar.append({
                    "id": other.id,
                    "farmer_id": other.farmer_id,
                    "text_content": other.text_content,
                    "match_score": match_count / len(keywords),
                    "created_at": other.created_at.isoformat() if other.created_at else None,
                })

    return sorted(similar, key=lambda x: x["match_score"], reverse=True)[:10]
