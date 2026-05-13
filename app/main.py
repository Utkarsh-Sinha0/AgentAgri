"""
AgriMesh V4.0 — Main FastAPI Application
Serves: PWA (extension worker dashboard), eval dashboard, health checks.
The Telegram bot runs separately (app/bot/telegram_bot.py).
"""
from __future__ import annotations

import asyncio
import secrets
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import async_session_factory, get_db, init_db
from app.models import (
    Advisory,
    AlertCluster,
    Farmer,
    WikiArticle,
)
from app.utils.security import get_api_limiter


class ClusterReviewRequest(BaseModel):
    action: Literal["approve_broadcast", "dismiss", "reviewed"]
    extension_worker_id: str = PydanticField(min_length=1, max_length=80)
    broadcast_message: str = PydanticField(default="", max_length=1200)


# ─── Lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown events."""
    logger.info("🌾 AgriMesh V4.0 starting up...")
    await init_db()
    logger.info("Database initialized.")

    from app.services.evidence import seed_source_registry
    async with async_session_factory() as db:
        await seed_source_registry(db)

    if settings.app_env.lower() == "test":
        yield
        return

    # Start Telegram bot in background (if token configured)
    if settings.telegram_bot_token and settings.telegram_bot_token != "your_bot_token_here":
        from app.bot.telegram_bot import run_bot
        bot_task = asyncio.create_task(run_bot())
        logger.info("Telegram bot started.")

    # Start proactive messaging background loop (§6.8)
    from app.services.proactive import proactive_loop
    proactive_task = asyncio.create_task(proactive_loop(interval_seconds=1800))
    logger.info("Proactive messaging started (30min interval).")

    # Start health monitor + degradation ladder (§7)
    from app.services.degradation import get_circuit_breaker, health_monitor_loop
    health_task = asyncio.create_task(health_monitor_loop(interval=60))
    cb = get_circuit_breaker()
    await cb.check_health()
    logger.info(f"Health monitor started. Current level: {cb.current_level.name}")

    yield

    logger.info("AgriMesh shutting down.")
    tasks = []
    for task_name in ["bot_task", "proactive_task", "health_task"]:
        if task_name in locals():
            task = locals()[task_name]
            task.cancel()
            tasks.append(task)
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


# ─── App ──────────────────────────────────────────────────────────────

app = FastAPI(
    title="AgriMesh V4.0",
    description="AI Agricultural Advisory for Smallholder Farmers — Gemma 4 E4B + RAG + MCP",
    version="4.0.0",
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None if settings.is_production else "/redoc",
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=settings.allowed_hosts,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Production Safety Middleware ─────────────────────────────────────

@app.middleware("http")
async def request_guardrails(request: Request, call_next):
    """Apply low-cost request limits and browser security headers."""
    content_length = request.headers.get("content-length")
    if content_length and int(content_length) > settings.max_request_bytes:
        return JSONResponse(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            content={"detail": "Request body too large"},
        )

    if request.url.path.startswith("/api/"):
        client_host = request.client.host if request.client else "unknown"
        key = f"{client_host}:{request.url.path}"
        if not get_api_limiter().is_allowed(key):
            return JSONResponse(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                content={"detail": "Rate limit exceeded"},
            )

    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()",
    )
    return response


async def require_api_key(
    x_agrimesh_api_key: str | None = Header(default=None),
) -> bool:
    """Fail closed for protected API routes when production auth is enabled."""
    if not settings.api_key_required:
        return True
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AGRIMESH_API_KEY must be configured when API key protection is enabled",
        )
    if not x_agrimesh_api_key or not secrets.compare_digest(x_agrimesh_api_key, settings.api_key):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid API key")
    return True


# ─── Health ───────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "version": "4.0.0",
        "model": settings.ollama_model,
        "grammar_decoding": settings.use_grammar_decoding,
        "environment": settings.app_env,
    }


@app.get("/api/health/degradation")
async def degradation_health(_: bool = Depends(require_api_key)):
    """Return current degradation ladder state for operators."""
    from app.services.degradation import get_circuit_breaker

    status_obj = await get_circuit_breaker().check_health()
    return {
        "level": int(status_obj.level),
        "level_name": status_obj.level.name,
        "ollama_healthy": status_obj.ollama_healthy,
        "ollama_model_available": status_obj.ollama_model_available,
        "vision_healthy": status_obj.vision_healthy,
        "wiki_available": status_obj.wiki_available,
        "tools_healthy": status_obj.tools_healthy,
        "db_healthy": status_obj.db_healthy,
        "latency_ms": status_obj.latency_ms,
        "last_checked": status_obj.last_checked,
    }


# ─── Extension Worker API ─────────────────────────────────────────────

@app.get("/api/clusters")
async def list_clusters(
    district: str | None = None,
    tehsil: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """List alert clusters for extension worker dashboard."""
    from app.services.cluster import get_pending_clusters
    clusters = await get_pending_clusters(db, district=district, tehsil=tehsil)
    return {
        "count": len(clusters),
        "clusters": [
            {
                "id": c.id,
                "district": c.district,
                "tehsil": c.tehsil,
                "crop_name": c.crop_name,
                "issue_category": c.issue_category,
                "farmer_count": c.farmer_count,
                "severity": c.severity,
                "status": c.status,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in clusters
        ],
    }


@app.get("/api/clusters/{cluster_id}")
async def cluster_detail(
    cluster_id: str,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Get full cluster details."""
    from app.services.cluster import get_cluster_details
    return await get_cluster_details(db, cluster_id)


@app.post("/api/clusters/{cluster_id}/review")
async def review_cluster_endpoint(
    cluster_id: str,
    request: ClusterReviewRequest,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Review and act on a cluster."""
    from app.services.cluster import review_cluster
    return await review_cluster(
        db,
        cluster_id,
        request.extension_worker_id,
        request.action,
        request.broadcast_message,
    )


# ─── Farmer API ───────────────────────────────────────────────────────

@app.get("/api/farmers/{farmer_id}/advisories")
async def farmer_advisories(
    farmer_id: str,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Get advisories for a farmer."""
    result = await db.execute(
        select(Advisory)
        .where(Advisory.farmer_id == farmer_id)
        .order_by(Advisory.created_at.desc())
        .limit(limit)
    )
    advisories = result.scalars().all()
    return {
        "count": len(advisories),
        "advisories": [
            {
                "id": a.id,
                "risk_level": a.risk_level,
                "confidence": a.confidence,
                "contextualization": a.contextualization,
                "actions_text": a.actions_text,
                "created_at": a.created_at.isoformat() if a.created_at else None,
                "verifier_passed": a.verifier_report.passes_all if a.verifier_report else None,
            }
            for a in advisories
        ],
    }


# ─── Eval Dashboard API ───────────────────────────────────────────────

@app.get("/api/eval/latest")
async def latest_eval(_: bool = Depends(require_api_key)):
    """Return latest eval run results."""
    eval_path = Path(settings.eval_dir) / "latest_results.json"
    if not eval_path.exists():
        return {"status": "no_eval_yet", "message": "Run make eval to generate results"}
    import json
    return json.loads(eval_path.read_text(encoding="utf-8"))


@app.get("/api/eval/history")
async def eval_history(_: bool = Depends(require_api_key)):
    """Return eval run history."""
    eval_dir = Path(settings.eval_dir)
    results = []
    for f in sorted(eval_dir.glob("results_*.json"), reverse=True)[:10]:
        import json
        data = json.loads(f.read_text(encoding="utf-8"))
        results.append({
            "timestamp": f.stem.replace("results_", ""),
            "faithfulness": data.get("faithfulness", 0),
            "answer_relevancy": data.get("answer_relevancy", 0),
            "safety_pass_rate": data.get("safety_pass_rate", 0),
            "median_latency_ms": data.get("median_latency_ms", 0),
        })
    return {"history": results}


# ─── Stats ────────────────────────────────────────────────────────────

@app.get("/api/stats")
async def system_stats(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """System statistics for dashboard."""
    farmer_count = (await db.execute(select(func.count(Farmer.id)))).scalar()
    advisory_count = (await db.execute(select(func.count(Advisory.id)))).scalar()
    cluster_count = (await db.execute(
        select(func.count(AlertCluster.id)).where(AlertCluster.status == "pending")
    )).scalar()
    wiki_count = (await db.execute(select(func.count(WikiArticle.id)))).scalar()
    from app.models_memory import MemoryAtom, MemorySummary, SourceDocument
    memory_atom_count = (await db.execute(select(func.count(MemoryAtom.id)))).scalar()
    memory_summary_count = (await db.execute(select(func.count(MemorySummary.id)))).scalar()
    source_count = (await db.execute(select(func.count(SourceDocument.id)))).scalar()

    return {
        "farmers": farmer_count,
        "advisories": advisory_count,
        "pending_clusters": cluster_count,
        "wiki_articles": wiki_count,
        "memory_atoms": memory_atom_count,
        "memory_summaries": memory_summary_count,
        "sources": source_count,
        "model": settings.ollama_model,
    }


@app.get("/api/memory/summaries")
async def memory_summaries(
    scale: str | None = None,
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """List living-memory summaries for the dashboard."""
    from app.models_memory import MemorySummary

    query = select(MemorySummary).order_by(MemorySummary.last_updated.desc()).limit(min(limit, 100))
    if scale:
        query = query.where(MemorySummary.scale == scale)
    result = await db.execute(query)
    summaries = result.scalars().all()
    return {
        "count": len(summaries),
        "summaries": [
            {
                "id": s.id,
                "scale": s.scale,
                "scale_id": s.scale_id,
                "title": s.title,
                "summary_text": s.summary_text,
                "key_patterns": s.key_patterns or [],
                "stats": s.stats or {},
                "atom_count": s.atom_count,
                "farmer_count": s.farmer_count,
                "field_count": s.field_count,
                "confidence": s.confidence,
                "is_public": s.is_public,
                "last_updated": s.last_updated.isoformat() if s.last_updated else None,
            }
            for s in summaries
        ],
    }


@app.get("/api/sources")
async def source_registry(
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Return registered evidence sources and freshness metadata."""
    from datetime import datetime

    from app.models_memory import SourceDocument

    result = await db.execute(select(SourceDocument).order_by(SourceDocument.trust_level, SourceDocument.source_name))
    sources = result.scalars().all()
    now = datetime.utcnow()
    return {
        "count": len(sources),
        "sources": [
            {
                "id": s.id,
                "source_name": s.source_name,
                "source_type": s.source_type,
                "url": s.url,
                "description": s.description,
                "trust_level": s.trust_level,
                "is_official": s.is_official,
                "freshness_ttl_hours": s.freshness_ttl_hours,
                "last_fetched": s.last_fetched.isoformat() if s.last_fetched else None,
                "age_hours": round((now - s.last_fetched).total_seconds() / 3600, 1) if s.last_fetched else None,
            }
            for s in sources
        ],
    }


# ─── Static (PWA) ─────────────────────────────────────────────────────

pwa_dir = Path(__file__).parent.parent / "pwa" / "dist"
if pwa_dir.exists():
    app.mount("/", StaticFiles(directory=str(pwa_dir), html=True), name="pwa")
else:
    @app.get("/")
    async def root():
        return {
            "name": "AgriMesh V4.0",
            "status": "API running",
            "docs": "/docs",
            "pwa": "Build PWA with: cd pwa && npm run build",
        }


# ─── Run ──────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=True)
