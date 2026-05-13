"""
AgriMesh V4.0 — Retrieval Pipeline Tests
Tests: SQL metadata filtering, BGE-M3 embedding, reranker, adaptive routing.
"""
import json
from pathlib import Path

import pytest
from sqlalchemy import select

from app.models import WikiArticle


@pytest.mark.asyncio
async def test_load_wiki_from_files(db_session):
    """Wiki articles can be loaded from JSON files into the database."""
    wiki_dir = Path(__file__).parent.parent / "wiki" / "articles"
    if not wiki_dir.exists():
        pytest.skip("Wiki directory not found")

    for article_file in wiki_dir.glob("*.json"):
        data = json.loads(article_file.read_text(encoding="utf-8"))
        article = WikiArticle(
            id=data["id"],
            title=data["title"],
            content=data["content"],
            summary=data.get("summary", ""),
            summary_hi=data.get("summary_hi"),
            applicable_crops=data.get("applicable_crops", []),
            applicable_stages=data.get("applicable_stages", []),
            topic_tags=data.get("topic_tags", []),
            risk_level=data.get("risk_level", "WATCH"),
            actions=data.get("actions", []),
            warnings=data.get("warnings", []),
            causes_of=data.get("causes_of", []),
            aggravated_by=data.get("aggravated_by", []),
            prevented_by=data.get("prevented_by", []),
            confused_with=data.get("confused_with", []),
            review_status="published",
            confidence_score=0.85,
        )
        db_session.add(article)

    await db_session.commit()

    result = await db_session.execute(select(WikiArticle))
    articles = result.scalars().all()
    assert len(articles) >= 3  # At least 3 articles loaded


@pytest.mark.asyncio
async def test_sql_metadata_filter_by_crop(db_session):
    """SQL metadata filter should return articles tagged for a specific crop or universal."""
    # Seed: one rice article, one universal article
    rice_article = WikiArticle(
        id="test_rice_1", title="Test Rice Article", content="test",
        applicable_crops=["rice"], topic_tags=["disease"],
        review_status="published", actions=[], warnings=[],
    )
    universal_article = WikiArticle(
        id="test_univ_1", title="Test Universal Article", content="test",
        applicable_crops=[], topic_tags=["fertilizer"],
        review_status="published", actions=[], warnings=[],
    )
    wheat_article = WikiArticle(
        id="test_wheat_1", title="Test Wheat Article", content="test",
        applicable_crops=["wheat"], topic_tags=["disease"],
        review_status="published", actions=[], warnings=[],
    )
    db_session.add_all([rice_article, universal_article, wheat_article])
    await db_session.commit()

    from app.services.retrieval import _sql_metadata_filter
    results = await _sql_metadata_filter(db_session, crop_name="rice")
    result_ids = {r.id for r in results}
    assert "test_rice_1" in result_ids
    assert "test_univ_1" in result_ids  # Universal articles included
    assert "test_wheat_1" not in result_ids  # Wheat-only excluded


@pytest.mark.asyncio
async def test_sql_metadata_filter_by_tags(db_session):
    """SQL metadata filter should return articles matching any of the given topic tags."""
    fungal = WikiArticle(
        id="test_fungal", title="Fungal", content="test",
        applicable_crops=[], topic_tags=["fungal_disease"],
        review_status="published", actions=[], warnings=[],
    )
    pest = WikiArticle(
        id="test_pest", title="Pest", content="test",
        applicable_crops=[], topic_tags=["pest"],
        review_status="published", actions=[], warnings=[],
    )
    db_session.add_all([fungal, pest])
    await db_session.commit()

    from app.services.retrieval import _sql_metadata_filter
    results = await _sql_metadata_filter(db_session, topic_tags=["fungal_disease"])
    result_ids = {r.id for r in results}
    assert "test_fungal" in result_ids
    assert "test_pest" not in result_ids


@pytest.mark.asyncio
async def test_keyword_retrieve(db_session):
    """Keyword retrieval should find articles containing query terms."""
    article = WikiArticle(
        id="test_kw", title="Rice Blast Management", content="Apply triazole fungicide at booting stage for rice blast control.",
        applicable_crops=["rice"], topic_tags=["fungal_disease"],
        review_status="published", actions=["Apply fungicide"], warnings=[],
    )
    db_session.add(article)
    await db_session.commit()

    from app.services.retrieval import keyword_retrieve
    results = await keyword_retrieve(db_session, "rice blast fungicide")
    assert len(results) >= 1
    assert results[0]["id"] == "test_kw"
