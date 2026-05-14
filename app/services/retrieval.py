"""
AgriMesh V4.0 — Retrieval Engine
BGE-M3 dense embedding + bge-reranker-v2-m3 cross-encoder +
graph traversal + adaptive routing (fast vs graph path).

Architecture:
  1. Adaptive router chooses fast or graph path based on query features
  2. SQL metadata filtering by crop/stage/tags
  3. BGE-M3 dense retrieval over filtered candidates
  4. Optional: graph traversal (±1 hop) for multi-hop queries
  5. bge-reranker-v2-m3 cross-encoder reranks top-K → top-3
  6. Load full articles for top-3 into context
"""
from __future__ import annotations

import asyncio
import json
import time
from typing import Literal

import numpy as np
from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import WikiArticle

# ─── Embedder & Reranker (lazy-loaded singletons) ─────────────────────

_embedder = None
_reranker = None


def _get_embedding_semaphore() -> asyncio.Semaphore:
    """Return a Semaphore bound to the running event loop.

    Stored on the loop itself so each event loop (including the fresh one pytest
    spins up per test) gets an independent Semaphore.
    """
    loop = asyncio.get_running_loop()
    sem = getattr(loop, "_agrimesh_embedding_semaphore", None)
    if sem is None:
        sem = asyncio.Semaphore(settings.embedding_max_concurrency)
        loop._agrimesh_embedding_semaphore = sem  # type: ignore[attr-defined]
    return sem


def get_embedder():
    """Lazy-load BGE-M3. First call downloads ~500 MB."""
    global _embedder
    if _embedder is None:
        from sentence_transformers import SentenceTransformer
        logger.info(f"Loading BGE-M3 embedder: {settings.bge_m3_model} ...")
        _embedder = SentenceTransformer(
            settings.bge_m3_model,
            device="cpu",  # CPU to avoid GPU contention with Gemma 4
        )
        logger.info("BGE-M3 ready.")
    return _embedder


def get_reranker():
    """Lazy-load bge-reranker-v2-m3. First call downloads ~400 MB."""
    global _reranker
    if _reranker is None:
        from FlagEmbedding import FlagReranker
        logger.info(f"Loading reranker: {settings.bge_reranker_model} ...")
        _reranker = FlagReranker(
            settings.bge_reranker_model,
            use_fp16=False,  # CPU mode
            device="cpu",
        )
        logger.info("Reranker ready.")
    return _reranker


_get_embedder = get_embedder
_get_reranker = get_reranker


# ─── Adaptive Router ──────────────────────────────────────────────────

ROUTER_KEYWORDS_MULTI_EVIDENCE = [
    "weather", "मौसम", "rain", "बारिश", "बारिश",
    "fertilizer", "उर्वरक", "खाद",
    "fungus", "फंगस", "फफूंद",
    "pest", "कीट",
    "soil", "मिट्टी",
    "irrigation", "सिंचाई",
]


def classify_retrieval_path(
    query: str,
    is_followup: bool = False,
) -> Literal["fast", "graph"]:
    """
    Adaptive retrieval routing.
    Returns 'graph' for multi-evidence or follow-up queries, 'fast' otherwise.
    Runs in ~1 ms.
    """
    ql = query.lower()
    evidence_count = sum(
        1 for kw in ROUTER_KEYWORDS_MULTI_EVIDENCE if kw in ql
    )
    is_multi_evidence = evidence_count >= 2

    if is_multi_evidence or is_followup:
        return "graph"
    return "fast"


# ─── SQL Metadata Filtering ───────────────────────────────────────────

async def _sql_metadata_filter(
    db: AsyncSession,
    crop_name: str | None = None,
    stage: str | None = None,
    topic_tags: list[str] | None = None,
    limit: int = 20,
) -> list[WikiArticle]:
    """
    Filter wiki articles by crop, stage, and topic tags.
    Returns matching articles (id, title, summary for embedding).
    """
    query = select(WikiArticle).where(WikiArticle.review_status == "published")

    if crop_name:
        # Articles tagged with this crop OR untagged (applicable to all)
        query = query.where(
            (WikiArticle.applicable_crops.contains([crop_name]))
            | (WikiArticle.applicable_crops == json.dumps([]))
        )

    if stage:
        query = query.where(
            (WikiArticle.applicable_stages.contains([stage]))
            | (WikiArticle.applicable_stages == json.dumps([]))
        )

    if topic_tags:
        # Match any of the provided tags
        tag_conditions = [
            WikiArticle.topic_tags.contains([tag]) for tag in topic_tags
        ]
        from sqlalchemy import or_
        query = query.where(or_(*tag_conditions))

    query = query.limit(limit)
    result = await db.execute(query)
    return list(result.scalars().all())


# ─── Dense Retrieval (BGE-M3) ─────────────────────────────────────────

async def _embed_query(query: str) -> np.ndarray:
    async with _get_embedding_semaphore():
        embedder = get_embedder()
        return await asyncio.to_thread(
            embedder.encode,
            query,
            normalize_embeddings=True,
        )


async def _embed_documents(texts: list[str]) -> np.ndarray:
    async with _get_embedding_semaphore():
        embedder = get_embedder()
        return await asyncio.to_thread(
            embedder.encode,
            texts,
            normalize_embeddings=True,
        )


async def _dense_retrieve(
    query: str,
    candidates: list[dict],
    top_k: int = 10,
) -> list[dict]:
    """
    BGE-M3 dense retrieval over candidate summaries.
    Each candidate dict must have 'id', 'title', 'summary'.
    Returns candidates sorted by cosine similarity, enriched with score.
    """
    if not candidates:
        return []

    query_vec = await _embed_query(query)
    summaries = [c["summary"] or c["title"] for c in candidates]
    doc_vecs = await _embed_documents(summaries)

    # Cosine similarity (vectors are already normalized)
    scores = np.dot(doc_vecs, query_vec)

    # Sort by score descending
    ranked = sorted(
        zip(candidates, scores, strict=False),
        key=lambda x: x[1],
        reverse=True,
    )[:top_k]

    result = []
    for cand, score in ranked:
        cand["_score"] = float(score)
        result.append(cand)
    return result


# ─── Reranker (bge-reranker-v2-m3) ────────────────────────────────────

async def _rerank(
    query: str,
    candidates: list[dict],
    top_k: int = 3,
) -> list[dict]:
    """Cross-encoder reranking of top candidates. ~50 ms for top-20."""
    if len(candidates) <= top_k:
        for c in candidates:
            c["_rerank_score"] = c.get("_score", 0.0)
        return candidates[:top_k]

    pairs = [[query, c.get("summary", "") or c.get("title", "")] for c in candidates]

    # Batch compute scores
    async with _get_embedding_semaphore():
        reranker = get_reranker()
        scores = await asyncio.to_thread(reranker.compute_score, pairs)
    if isinstance(scores, float):
        scores = [scores]

    # Sort by reranker score
    reranked = sorted(
        zip(candidates, scores, strict=False),
        key=lambda x: x[1],
        reverse=True,
    )
    result = []
    for cand, score in reranked[:top_k]:
        cand["_rerank_score"] = float(score)
        result.append(cand)
    return result


# ─── Graph Traversal ──────────────────────────────────────────────────

async def _graph_traverse(
    db: AsyncSession,
    article_ids: list[str],
    hops: int = 1,
) -> list[str]:
    """
    Walk the graph: causes_of, aggravated_by, prevented_by, confused_with edges.
    Returns expanded set of article IDs (±1 hop from inputs).
    """
    expanded = set(article_ids)
    for _ in range(hops):
        new_ids = set()
        for aid in list(expanded):
            result = await db.execute(
                select(WikiArticle).where(WikiArticle.id == aid)
            )
            article = result.scalar_one_or_none()
            if article:
                edges = (
                    (article.causes_of or [])
                    + (article.aggravated_by or [])
                    + (article.prevented_by or [])
                    + (article.confused_with or [])
                    + (article.correlated_with or [])
                    + (article.followed_by or [])
                    + (article.treated_by or [])
                    + (article.variant_of or [])
                    + (article.regional_of or [])
                )
                new_ids.update(edges)
        expanded.update(new_ids)

    return list(expanded)


# ─── Main Retrieval Pipeline ──────────────────────────────────────────

async def retrieve(
    db: AsyncSession,
    query: str,
    crop_name: str | None = None,
    stage: str | None = None,
    topic_tags: list[str] | None = None,
    is_followup: bool = False,
    previous_article_ids: list[str] | None = None,
) -> dict:
    """
    Full retrieval pipeline with adaptive routing.

    Returns:
        dict with:
            articles: list[dict] — top-3 full wiki articles (id, title, content, actions, warnings, etc.)
            path: str — 'fast' or 'graph'
            latency_ms: int
            candidate_count: int
    """
    t0 = time.perf_counter()
    path = classify_retrieval_path(query, is_followup)
    logger.debug(f"Retrieval path: {path} for query: {query[:80]}")

    # ── Step 1: SQL metadata filter ──────────────────────────────
    db_articles = await _sql_metadata_filter(db, crop_name, stage, topic_tags)
    if not db_articles:
        # Fallback: retrieve all published articles
        db_articles = await _sql_metadata_filter(db)

    # ── Step 2: Graph expansion (graph path only) ────────────────
    article_id_set = {a.id for a in db_articles}
    if path == "graph" and previous_article_ids:
        expanded = await _graph_traverse(db, previous_article_ids, hops=1)
        # Fetch additional articles from graph traversal
        if expanded - article_id_set:
            result = await db.execute(
                select(WikiArticle).where(WikiArticle.id.in_(list(expanded - article_id_set)))
            )
            db_articles.extend(list(result.scalars().all()))

    # ── Step 3: Build candidate list for embedding ───────────────
    candidates = [
        {
            "id": a.id,
            "title": a.title,
            "title_hi": a.title_hi,
            "summary": a.summary or a.content[:300],
            "summary_hi": a.summary_hi,
            "content": a.content,
            "content_hi": a.content_hi,
            "actions": a.actions or [],
            "warnings": a.warnings or [],
            "topic_tags": a.topic_tags or [],
            "applicable_crops": a.applicable_crops or [],
            "applicable_stages": a.applicable_stages or [],
            "risk_level": a.risk_level,
            "causes_of": a.causes_of or [],
            "aggravated_by": a.aggravated_by or [],
            "prevented_by": a.prevented_by or [],
        }
        for a in db_articles
    ]

    # ── Step 4: BGE-M3 dense retrieval ───────────────────────────
    ranked = await _dense_retrieve(query, candidates, top_k=settings.retrieval_top_k)

    # ── Step 5: Reranker (cross-encoder) ─────────────────────────
    final = await _rerank(query, ranked, top_k=settings.reranker_top_k)

    # ── Step 6: Clean scores from output ─────────────────────────
    for art in final:
        art.pop("_score", None)
        art.pop("_rerank_score", None)

    latency_ms = int((time.perf_counter() - t0) * 1000)
    logger.info(
        f"Retrieval complete: path={path}, candidates={len(candidates)}, "
        f"final={len(final)}, latency={latency_ms}ms"
    )

    return {
        "articles": final,
        "path": path,
        "latency_ms": latency_ms,
        "candidate_count": len(candidates),
    }


# ─── Speculative Retrieval (parallel warm cache) ──────────────────────

async def speculative_retrieve(
    db: AsyncSession,
    query: str,
    **kwargs,
):
    """
    Fire retrieval in parallel while the LLM decides what to do.
    If LLM chooses retrieval, results are warm. If not, discard.
    """
    task = asyncio.create_task(retrieve(db, query, **kwargs))
    return task  # Caller awaits when ready or cancels


# ─── Simple keyword retrieval (Level 1 fallback, no embeddings) ───────

async def keyword_retrieve(
    db: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[dict]:
    """Fast keyword-based retrieval using PostgreSQL full-text or simple ILIKE."""
    keywords = [kw for kw in query.lower().split() if kw.strip()]
    if not keywords:
        return []
    conditions = []
    for kw in keywords[:5]:  # Top 5 keywords
        conditions.append(WikiArticle.content.ilike(f"%{kw}%"))

    from sqlalchemy import or_
    result = await db.execute(
        select(WikiArticle)
        .where(or_(*conditions))
        .where(WikiArticle.review_status == "published")
    )
    articles = result.scalars().all()
    ranked = sorted(
        articles,
        key=lambda article: _keyword_score(article, keywords),
        reverse=True,
    )[:limit]
    return [
        {
            "id": a.id,
            "title": a.title,
            "content": a.content,
            "actions": a.actions or [],
            "warnings": a.warnings or [],
        }
        for a in ranked
    ]


def _keyword_score(article: WikiArticle, keywords: list[str]) -> int:
    haystack = " ".join(
        [
            article.title or "",
            article.summary or "",
            article.content or "",
            " ".join(article.topic_tags or []),
        ]
    ).lower()
    return sum(haystack.count(keyword) for keyword in keywords[:5])
