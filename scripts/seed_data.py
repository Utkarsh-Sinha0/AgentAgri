"""
AgriMesh V4.0 — Database Seed Script
Seeds: wiki articles, demo farmer, demo field, weather/mandi/scheme data.
Usage: python scripts/seed_data.py [--wiki-only]
"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from loguru import logger
from sqlalchemy import select

from app.config import settings
from app.database import async_session_factory, init_db
from app.models import (
    AlertCluster,
    AlertStatus,
    CropCalendarTask,
    CropCycle,
    Farmer,
    Field,
    SatelliteNDVI,
    WikiArticle,
)
from app.services.demo_seed import seed_demo_memory_palace
from app.utils.security import hash_password


async def load_wiki_articles():
    """Load all JSON wiki articles from wiki/articles/ into the database."""
    wiki_dir = settings.wiki_dir
    if not wiki_dir.exists():
        logger.warning(f"Wiki directory not found: {wiki_dir}")
        return

    async with async_session_factory() as db:
        for article_file in wiki_dir.glob("*.json"):
            data = json.loads(article_file.read_text(encoding="utf-8"))
            article_id = data["id"]

            # Check if already exists
            result = await db.execute(
                select(WikiArticle).where(WikiArticle.id == article_id)
            )
            if result.scalar_one_or_none():
                logger.info(f"  Skipping existing: {article_id}")
                continue

            article = WikiArticle(
                id=article_id,
                title=data["title"],
                title_hi=data.get("title_hi"),
                content=data["content"],
                content_hi=data.get("content_hi"),
                summary=data.get("summary"),
                summary_hi=data.get("summary_hi"),
                applicable_crops=data.get("applicable_crops", []),
                applicable_stages=data.get("applicable_stages", []),
                topic_tags=data.get("topic_tags", []),
                risk_level=data.get("risk_level", "WATCH"),
                causes_of=data.get("causes_of", []),
                aggravated_by=data.get("aggravated_by", []),
                prevented_by=data.get("prevented_by", []),
                correlated_with=data.get("correlated_with", []),
                followed_by=data.get("followed_by", []),
                treated_by=data.get("treated_by", []),
                variant_of=data.get("variant_of", []),
                regional_of=data.get("regional_of", []),
                confused_with=data.get("confused_with", []),
                actions=data.get("actions", []),
                warnings=data.get("warnings", []),
                source_url=data.get("source_url"),
                review_status="published",
                confidence_score=0.85,
            )
            db.add(article)
            logger.info(f"  Loaded: {article_id} — {data['title']}")

        await db.commit()
    logger.info("Wiki articles loaded.")


async def seed_demo_data():
    """Create demo farmer, field, and crop cycle."""
    import uuid
    from datetime import datetime, timedelta

    async with async_session_factory() as db:
        # Check if demo farmer exists
        result = await db.execute(
            select(Farmer).where(Farmer.phone == "demo_farmer")
        )
        if result.scalar_one_or_none():
            logger.info("Demo data already exists. Skipping.")
            return

        farmer_id = str(uuid.uuid4())
        farmer = Farmer(
            id=farmer_id,
            phone="demo_farmer",
            hashed_password=hash_password("demo"),
            name="Ram Kumar",
            preferred_language="hi",
            district="Munger",
            tehsil="Munger Sadar",
            village="Bariarpur",
        )
        db.add(farmer)

        field_id = str(uuid.uuid4())
        field = Field(
            id=field_id,
            farmer_id=farmer_id,
            name="Purab Wala Khet",
            area_acres=2.5,
            soil_type="clay_loam",
            soil_ph=6.8,
            lat=25.38,
            lng=86.47,
            irrigation_type="canal",
        )
        db.add(field)

        cycle_id = str(uuid.uuid4())
        cycle = CropCycle(
            id=cycle_id,
            field_id=field_id,
            crop_name="rice",
            variety="Swarna",
            sowing_date=datetime.utcnow() - timedelta(days=45),
            expected_harvest_date=datetime.utcnow() + timedelta(days=75),
            current_stage="vegetative",
            is_active=True,
        )
        db.add(cycle)

        # Add crop calendar tasks
        tasks = [
            ("seedling", "Nursery preparation", 0),
            ("seedling", "Transplanting", 21),
            ("vegetative", "First urea top-dressing", 30),
            ("vegetative", "Weeding", 35),
            ("vegetative", "Irrigation check", 40),
            ("flowering", "Second urea top-dressing (if needed)", 55),
            ("flowering", "Pest scouting", 60),
            ("fruiting", "Water management — drain 10 days before harvest", 90),
            ("harvest", "Harvest when 80% grains are golden", 110),
        ]
        for stage, task, days in tasks:
            t = CropCalendarTask(
                id=str(uuid.uuid4()),
                cycle_id=cycle_id,
                stage=stage,
                task_name=task,
                days_from_sowing=days,
            )
            db.add(t)

        # Create a sample alert cluster
        cluster = AlertCluster(
            id=str(uuid.uuid4()),
            district="Munger",
            tehsil="Munger Sadar",
            village="Bariarpur",
            crop_name="rice",
            issue_category="fungal_disease",
            observation_ids=[],
            advisory_ids=[],
            farmer_count=3,
            severity=0.72,
            status=AlertStatus.PENDING,
        )
        db.add(cluster)

        # Seed NDVI data for the field
        import json as _json
        ndvi_path = settings.data_dir / "ndvi_seed.json"
        if ndvi_path.exists():
            ndvi_data = _json.loads(ndvi_path.read_text(encoding="utf-8"))
            for _field_key, field_ndvi in ndvi_data.items():
                for week in field_ndvi.get("ndvi_weekly", []):
                    ndvi_entry = SatelliteNDVI(
                        id=str(uuid.uuid4()),
                        field_id=field_id,
                        date=datetime.fromisoformat(week["date"]),
                        ndvi_value=week["ndvi"],
                        source="seeded",
                    )
                    db.add(ndvi_entry)

        await db.commit()
        logger.info(f"Demo data seeded: farmer={farmer_id}, field={field_id}, cycle={cycle_id}")


async def main():
    """Run all seed operations."""
    wiki_only = "--wiki-only" in sys.argv

    logger.info("Initializing database...")
    await init_db()

    if not wiki_only:
        logger.info("Seeding demo data...")
        await seed_demo_data()
        logger.info("Seeding demo memory palace...")
        async with async_session_factory() as db:
            summary = await seed_demo_memory_palace(db, telegram_user_id="demo_farmer")
            logger.info(
                "Demo memory palace ready: farmer={farmer_id}, observations={observations}, "
                "ndvi_points={ndvi_points}".format(**summary)
            )

    logger.info("Loading wiki articles...")
    await load_wiki_articles()

    logger.info("Seeding source registry...")
    from app.services.evidence import seed_source_registry
    async with async_session_factory() as db2:
        await seed_source_registry(db2)

    logger.info("Running memory backfill...")
    from app.services.memory import seed_memory_from_existing
    async with async_session_factory() as db3:
        await seed_memory_from_existing(db3)

    logger.info("Running coarsening job...")
    from app.services.memory import run_coarsening_job
    async with async_session_factory() as db4:
        await run_coarsening_job(db4)

    logger.info("✅ Seed complete!")


if __name__ == "__main__":
    asyncio.run(main())
