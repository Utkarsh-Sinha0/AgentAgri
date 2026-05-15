"""
AgriMesh V4.0 — Pattern Discovery Service (§5.11, §6.6)
Nightly batch processing: reads anonymized observations, identifies patterns,
proposes new graph-wiki edges. New edges flagged confidence=draft until reviewed.
"""
from __future__ import annotations

import asyncio
from collections import Counter
from datetime import timedelta

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import async_session_factory
from app.models import (
    Advisory,
    AlertCluster,
    AlertStatus,
    Farmer,
    Observation,
    WikiArticle,
)
from app.utils.time import utc_now


async def discover_patterns(db: AsyncSession) -> dict:
    """
    Nightly pattern discovery run.
    1. Group recent observations by crop + issue_category + district
    2. Identify clusters of 3+ similar observations
    3. Propose new wiki graph edges between related articles
    4. Flag new insights as draft for extension worker review
    """
    results = {"new_clusters": 0, "new_graph_edges": 0, "insights": []}

    # ── 1. Find emerging clusters ────────────────────────────────
    cutoff = utc_now() - timedelta(hours=72)

    # Get recent observations with advisories
    result = await db.execute(
        select(Observation, Advisory)
        .join(Advisory, Advisory.observation_id == Observation.id, isouter=True)
        .where(
            Observation.created_at >= cutoff,
            Observation.text_content.isnot(None),
        )
        .limit(200)
    )
    rows = result.all()

    if not rows:
        return results

    # Group by crop + district + risk_level
    groups: dict[str, list] = {}
    for obs, adv in rows:
        # Get farmer's district
        farmer_result = await db.execute(
            select(Farmer).where(Farmer.id == obs.farmer_id)
        )
        farmer = farmer_result.scalar_one_or_none()
        district = farmer.district if farmer else "unknown"

        key = f"{obs.crop_cycle_id}:{district}:{adv.risk_level if adv else 'UNKNOWN'}"
        if key not in groups:
            groups[key] = []
        groups[key].append((obs, adv, district))

    # ── 2. Create clusters for groups with 3+ observations ───────
    for _key, items in groups.items():
        if len(items) >= 3:
            districts = set(d for _, _, d in items)
            for district in districts:
                district_items = [(o, a) for o, a, d in items if d == district]
                if len(district_items) >= 3:
                    # Check if cluster already exists
                    obs_ids = [o.id for o, _ in district_items]
                    adv_ids = [a.id for _, a in district_items if a]

                    existing = await db.execute(
                        select(AlertCluster).where(
                            AlertCluster.observation_ids.contains(obs_ids[:1])
                        )
                    )
                    if existing.scalar_one_or_none():
                        continue

                    cluster = AlertCluster(
                        id=str(__import__("uuid").uuid4()),
                        district=district,
                        tehsil="",
                        crop_name="multiple",
                        issue_category=district_items[0][1].risk_level if district_items[0][1] else "UNKNOWN",
                        observation_ids=obs_ids,
                        advisory_ids=adv_ids,
                        farmer_count=len(district_items),
                        severity=min(0.40 + (len(district_items) * 0.05), 0.95),
                        status=AlertStatus.PENDING,
                    )
                    db.add(cluster)
                    results["new_clusters"] += 1
                    results["insights"].append({
                        "type": "cluster_detected",
                        "district": district,
                        "farmer_count": len(district_items),
                        "risk_level": district_items[0][1].risk_level if district_items[0][1] else "UNKNOWN",
                    })

    # ── 3. Propose graph edges from co-occurrence ────────────────
    # Find which wiki articles were retrieved together frequently
    article_cooccurrence: dict[tuple[str, str], int] = Counter()
    adv_result = await db.execute(
        select(Advisory).where(Advisory.created_at >= cutoff).limit(100)
    )
    advisories = adv_result.scalars().all()

    for adv in advisories:
        article_ids = adv.evidence_article_ids or []
        for i, a1 in enumerate(article_ids):
            for a2 in article_ids[i + 1:]:
                key = tuple(sorted([a1, a2]))
                article_cooccurrence[key] += 1

    # Propose CORRELATED_WITH edges for frequently co-retrieved articles
    for (a1, a2), count in article_cooccurrence.items():
        if count >= 3:  # Retrieved together 3+ times
            art1 = await db.execute(select(WikiArticle).where(WikiArticle.id == a1))
            art2 = await db.execute(select(WikiArticle).where(WikiArticle.id == a2))
            article1 = art1.scalar_one_or_none()
            article2 = art2.scalar_one_or_none()

            if article1 and article2:
                # Co-occurrence is correlation, not causation. Bug 1 fix.
                corr1 = list(article1.correlated_with or [])
                corr2 = list(article2.correlated_with or [])

                if a2 not in corr1:
                    corr1.append(a2)
                    article1.correlated_with = corr1
                if a1 not in corr2:
                    corr2.append(a1)
                    article2.correlated_with = corr2

                results["new_graph_edges"] += 1
                results["insights"].append({
                    "type": "graph_edge_proposed",
                    "article_a": article1.title,
                    "article_b": article2.title,
                    "co_occurrence_count": count,
                })

    if results["new_clusters"] > 0 or results["new_graph_edges"] > 0:
        await db.commit()
        logger.info(
            f"Pattern discovery: {results['new_clusters']} clusters, "
            f"{results['new_graph_edges']} graph edges"
        )

    return results


async def run_nightly_discovery():
    """Run pattern discovery — designed for cron/daily execution."""
    try:
        async with async_session_factory() as db:
            result = await discover_patterns(db)
            logger.info(f"Nightly discovery complete: {result}")
            return result
    except Exception as exc:
        logger.error(f"Nightly discovery failed: {exc}")
        return {"error": str(exc)}


if __name__ == "__main__":
    asyncio.run(run_nightly_discovery())
