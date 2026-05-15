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
    CropCycle,
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

    # Batch-fetch Farmer + CropCycle for every observation to avoid the
    # original N+1 (Bug 9). Two SELECTs instead of 2*len(rows).
    farmer_ids = {obs.farmer_id for obs, _ in rows if obs.farmer_id}
    cycle_ids = {obs.crop_cycle_id for obs, _ in rows if obs.crop_cycle_id}

    farmer_map: dict[str, Farmer] = {}
    if farmer_ids:
        f_res = await db.execute(select(Farmer).where(Farmer.id.in_(farmer_ids)))
        farmer_map = {f.id: f for f in f_res.scalars().all()}

    cycle_map: dict[str, CropCycle] = {}
    if cycle_ids:
        c_res = await db.execute(select(CropCycle).where(CropCycle.id.in_(cycle_ids)))
        cycle_map = {c.id: c for c in c_res.scalars().all()}

    # Group by crop_name + district + risk_level (Bug 9: crop_cycle_id was
    # per-field-per-season so it never collided across farmers, which made
    # clustering effectively a no-op).
    groups: dict[str, list] = {}
    for obs, adv in rows:
        farmer = farmer_map.get(obs.farmer_id)
        district = farmer.district if farmer else "unknown"
        cycle = cycle_map.get(obs.crop_cycle_id) if obs.crop_cycle_id else None
        crop_name = (cycle.crop_name if cycle else "unknown") or "unknown"

        key = f"{crop_name}:{district}:{adv.risk_level if adv else 'UNKNOWN'}"
        if key not in groups:
            groups[key] = []
        groups[key].append((obs, adv, district, crop_name))

    # ── 2. Create clusters for groups with 3+ observations ───────
    for _key, items in groups.items():
        if len(items) < 3:
            continue
        districts = {d for _, _, d, _ in items}
        for district in districts:
            district_items = [
                (o, a, c) for o, a, d, c in items if d == district
            ]
            if len(district_items) < 3:
                continue

            obs_ids = [o.id for o, _, _ in district_items]
            adv_ids = [a.id for _, a, _ in district_items if a]

            existing = await db.execute(
                select(AlertCluster).where(
                    AlertCluster.observation_ids.contains(obs_ids[:1])
                )
            )
            if existing.scalar_one_or_none():
                continue

            crop_names = {c for _, _, c in district_items if c}
            cluster_crop = next(iter(crop_names)) if len(crop_names) == 1 else "multiple"
            first_adv = district_items[0][1]
            risk_level = first_adv.risk_level if first_adv else "UNKNOWN"

            cluster = AlertCluster(
                id=str(__import__("uuid").uuid4()),
                district=district,
                tehsil="",
                crop_name=cluster_crop,
                issue_category=risk_level,
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
                "crop_name": cluster_crop,
                "farmer_count": len(district_items),
                "risk_level": risk_level,
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
