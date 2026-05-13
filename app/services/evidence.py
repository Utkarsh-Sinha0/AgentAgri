"""
AgriMesh V4.0 — Evidence & Source Registry (§ Evidence And Data Backbone)
Official/high-trust sources first: Agmarknet, Soil Health Card, NASA POWER, ICAR, PM-KISAN, PMFBY.
Every advisory must cite at least one source with freshness tracking.
"""
from __future__ import annotations

from datetime import datetime

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models_memory import SourceCitation, SourceDocument

# ═══════════════════════════════════════════════════════════════════════
# OFFICIAL SOURCE REGISTRY
# ═══════════════════════════════════════════════════════════════════════

OFFICIAL_SOURCES: list[dict] = [
    {
        "source_name": "Agmarknet — Daily Mandi Prices",
        "source_type": "official_portal",
        "url": "https://delhi.data.gov.in/catalog/current-daily-price-various-commodities-various-markets-mandi",
        "description": "Government of India official daily mandi commodity prices via data.gov.in",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 24,
    },
    {
        "source_name": "Soil Health Card Portal",
        "source_type": "official_portal",
        "url": "https://www.nic.gov.in/project/soil-health-card-portal/",
        "description": "National Soil Health Card scheme — free soil testing and fertilizer recommendations",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 8760,  # 1 year (soil tests done seasonally)
    },
    {
        "source_name": "NASA POWER Daily Agroclimatology",
        "source_type": "official_portal",
        "url": "https://power.larc.nasa.gov/docs/services/api/temporal/daily/",
        "description": "NASA POWER daily weather data: temperature, humidity, rainfall, solar radiation",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 6,
    },
    {
        "source_name": "ICAR Kharif Agro-Advisories 2025",
        "source_type": "icar_advisory",
        "url": "https://icar.gov.in/en/icar-kharif-agro-advisories-farmers-2025",
        "description": "Indian Council of Agricultural Research — official crop advisories for Kharif season",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 168,  # 1 week
    },
    {
        "source_name": "PM-KISAN — NIC Portal",
        "source_type": "official_portal",
        "url": "https://www.nic.gov.in/project/pm-kisan/",
        "description": "PM-KISAN scheme: ₹6,000/year income support for small & marginal farmers",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 720,  # 30 days (scheme rules change infrequently)
    },
    {
        "source_name": "PM Fasal Bima Yojana (PMFBY)",
        "source_type": "official_portal",
        "url": "https://pmfby.gov.in",
        "description": "Crop insurance at 2% (Kharif) / 1.5% (Rabi) premium",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 720,
    },
    {
        "source_name": "FAOSTAT API",
        "source_type": "official_portal",
        "url": "https://github.com/FAOSTAT/faostat-api",
        "description": "UN Food and Agriculture Organization — global crop production and trade statistics",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 8760,  # Annual data
    },
    {
        "source_name": "FAOSTAT API Developer Portal 2026",
        "source_type": "official_portal",
        "url": "https://www.fao.org/statistics/highlights-archive/highlights-detail/faostat-launches-a-new-api-developer-portal-to-make-data-access-easier/en",
        "description": "FAO API portal for machine-readable global food and agriculture data in JSON/CSV.",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 8760,
    },
    {
        "source_name": "IMD API Management Platform",
        "source_type": "official_portal",
        "url": "https://api.imd.gov.in/",
        "description": "Official India Meteorological Department gateway for forecasts, observations, rainfall, and warnings.",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 6,
    },
    {
        "source_name": "World Bank AI for Agricultural Transformation",
        "source_type": "product_research",
        "url": "https://live.worldbank.org/en/event/2025/artificial-intelligence-foundations-agriculture-from-farms-to-future-economies",
        "description": "World Bank agriculture AI evidence emphasizing small AI, local context, partnerships, and resilience.",
        "trust_level": "high",
        "is_official": True,
        "freshness_ttl_hours": 8760,
    },
    {
        "source_name": "AgriRegion Region-Aware RAG",
        "source_type": "research_paper",
        "url": "https://arxiv.org/abs/2512.10114",
        "description": "Research paper motivating geospatial metadata injection and region-prioritized reranking for agricultural advice.",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 8760,
    },
    {
        "source_name": "AgriGPT Tri-RAG Agriculture LLM Ecosystem",
        "source_type": "research_paper",
        "url": "https://arxiv.org/abs/2508.08632",
        "description": "Research paper motivating dense + sparse + multi-hop graph retrieval and domain-specific agricultural evaluation.",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 8760,
    },
    {
        "source_name": "India Agricultural Data Infrastructure Review 2026",
        "source_type": "research_paper",
        "url": "https://arxiv.org/abs/2603.23289",
        "description": "Research paper highlighting machine readability, geocode fragmentation, and decision-cycle timing constraints in Indian agricultural data.",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 8760,
    },
    {
        "source_name": "AgriMesh Weather Tool (Seeded)",
        "source_type": "weather_tool",
        "url": None,
        "description": "Local weather forecast data (seeded — future: NASA POWER / IMD API)",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 6,
    },
    {
        "source_name": "AgriMesh Mandi Tool (Seeded)",
        "source_type": "mandi_tool",
        "url": None,
        "description": "Local mandi price data (seeded — future: Agmarknet API)",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 24,
    },
    {
        "source_name": "AgriMesh Graph-Wiki",
        "source_type": "wiki_article",
        "url": None,
        "description": "Expert-authored agricultural knowledge articles with graph relationships",
        "trust_level": "medium",
        "is_official": False,
        "freshness_ttl_hours": 2160,  # 90 days
    },
]


# ═══════════════════════════════════════════════════════════════════════
# SOURCE REGISTRY SERVICE
# ═══════════════════════════════════════════════════════════════════════

async def seed_source_registry(db: AsyncSession) -> int:
    """Register all official sources in the database. Idempotent."""
    count = 0
    for src in OFFICIAL_SOURCES:
        existing = await db.execute(
            select(SourceDocument).where(SourceDocument.source_name == src["source_name"])
        )
        if existing.scalar_one_or_none():
            continue

        doc = SourceDocument(
            source_name=src["source_name"],
            source_type=src["source_type"],
            url=src.get("url"),
            description=src.get("description", ""),
            trust_level=src.get("trust_level", "medium"),
            is_official=src.get("is_official", False),
            freshness_ttl_hours=src.get("freshness_ttl_hours", 24),
            last_fetched=datetime.utcnow(),
        )
        db.add(doc)
        count += 1

    if count > 0:
        await db.commit()
        logger.info(f"Registered {count} new source documents")
    return count


async def get_source_by_name(db: AsyncSession, source_name: str) -> SourceDocument | None:
    """Look up a source document by name."""
    result = await db.execute(
        select(SourceDocument).where(SourceDocument.source_name == source_name)
    )
    return result.scalar_one_or_none()


async def create_citation(
    db: AsyncSession,
    advisory_id: str,
    source_name: str,
    evidence_snapshot: str = "",
    relevance_score: float = 0.8,
    citation_context: str = "",
) -> SourceCitation | None:
    """Create a citation linking an advisory to a source."""
    source = await get_source_by_name(db, source_name)
    if not source:
        # Auto-register on first use
        source = SourceDocument(
            source_name=source_name,
            source_type="external",
            trust_level="low",
            is_official=False,
            freshness_ttl_hours=24,
            last_fetched=datetime.utcnow(),
        )
        db.add(source)
        await db.flush()

    citation = SourceCitation(
        advisory_id=advisory_id,
        source_document_id=source.id,
        evidence_snapshot=evidence_snapshot,
        relevance_score=relevance_score,
        citation_context=citation_context,
    )
    db.add(citation)
    return citation


async def build_evidence_cards_for_advisory(
    db: AsyncSession,
    advisory_id: str,
    weather_data: dict | None = None,
    mandi_data: dict | None = None,
    wiki_articles: list[dict] | None = None,
    scheme_data: dict | None = None,
    ndvi_data: dict | None = None,
    memory_context: list[dict] | None = None,
) -> list[dict]:
    """
    Build evidence cards with full source citations for display.
    Each card has: type, label, content, source_name, trust_level, freshness.
    """
    cards = []

    # Weather card
    if weather_data:
        await create_citation(db, advisory_id, "AgriMesh Weather Tool (Seeded)",
            evidence_snapshot=str(weather_data)[:200],
            citation_context="Weather data used in risk assessment")
        cards.append({
            "type": "weather",
            "label": "🌦️ Weather",
            "source_name": "AgriMesh Weather Tool (Seeded)",
            "trust_level": "medium",
            "freshness_hours": 6,
            "content": str(weather_data)[:300],
        })

    # Mandi card
    if mandi_data:
        await create_citation(db, advisory_id, "AgriMesh Mandi Tool (Seeded)",
            evidence_snapshot=str(mandi_data)[:200],
            citation_context="Market price data referenced")
        cards.append({
            "type": "mandi",
            "label": "🏪 Mandi Prices",
            "source_name": "AgriMesh Mandi Tool (Seeded)",
            "trust_level": "medium",
            "freshness_hours": 24,
            "content": str(mandi_data)[:300],
        })

    # Wiki article cards
    if wiki_articles:
        for art in wiki_articles[:3]:
            await create_citation(db, advisory_id, "AgriMesh Graph-Wiki",
                evidence_snapshot=art.get("summary", art.get("title", ""))[:200],
                citation_context=f"Wiki article: {art.get('title', 'Unknown')}")
            cards.append({
                "type": "wiki",
                "label": f"📚 {art.get('title', 'Article')}",
                "source_name": "AgriMesh Graph-Wiki",
                "trust_level": "medium",
                "freshness_hours": 2160,
                "content": art.get("summary", "")[:200],
            })

    # Scheme card
    if scheme_data:
        await create_citation(db, advisory_id, "PM-KISAN — NIC Portal",
            evidence_snapshot=str(scheme_data)[:200],
            citation_context="Government scheme eligibility data")
        cards.append({
            "type": "scheme",
            "label": "📋 Government Scheme",
            "source_name": "PM-KISAN — NIC Portal",
            "trust_level": "high",
            "freshness_hours": 720,
            "content": str(scheme_data)[:300],
        })

    # NDVI card
    if ndvi_data:
        await create_citation(db, advisory_id, "NASA POWER Daily Agroclimatology",
            evidence_snapshot=str(ndvi_data)[:200],
            citation_context="Satellite NDVI vegetation index")
        cards.append({
            "type": "satellite",
            "label": "🛰️ Satellite NDVI",
            "source_name": "NASA POWER Daily Agroclimatology",
            "trust_level": "high",
            "freshness_hours": 168,
            "content": f"NDVI: {ndvi_data.get('latest_ndvi', 'N/A')}, Trend: {ndvi_data.get('trend', 'N/A')}",
        })

    # Memory card
    if memory_context:
        for mem in memory_context[:2]:
            cards.append({
                "type": "memory",
                "label": "🧠 Field Memory",
                "source_name": "AgriMesh Living Memory",
                "trust_level": "medium",
                "freshness_hours": 8760,
                "content": mem.get("summary", "")[:200],
            })

    return cards


async def check_source_freshness(source_name: str) -> dict:
    """Check if a source's data is still within freshness TTL."""
    from app.database import async_session_factory
    async with async_session_factory() as db:
        result = await db.execute(
            select(SourceDocument).where(SourceDocument.source_name == source_name)
        )
        source = result.scalar_one_or_none()
        if not source or not source.last_fetched:
            return {"source": source_name, "fresh": False, "reason": "Source not found or never fetched"}

        age_hours = (datetime.utcnow() - source.last_fetched).total_seconds() / 3600
        is_fresh = age_hours <= source.freshness_ttl_hours
        return {
            "source": source_name,
            "fresh": is_fresh,
            "age_hours": round(age_hours, 1),
            "ttl_hours": source.freshness_ttl_hours,
            "trust_level": source.trust_level,
        }
