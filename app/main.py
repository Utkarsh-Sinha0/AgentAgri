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
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from loguru import logger
from pydantic import BaseModel, field_validator
from pydantic import Field as PydanticField
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.auth import router as auth_router
from app.config import settings
from app.database import async_session_factory, get_db, init_db
from app.models import (
    Advisory,
    AlertCluster,
    Farmer,
    WikiArticle,
)
from app.utils.security import get_api_limiter
from app.utils.time import utc_now


class ClusterReviewRequest(BaseModel):
    action: Literal["approve_broadcast", "dismiss", "reviewed"]
    extension_worker_id: str = PydanticField(min_length=1, max_length=80)
    broadcast_message: str = PydanticField(default="", max_length=1200)


class FarmerProfileUpdate(BaseModel):
    farm_size_acres: float | None = PydanticField(default=None, ge=0, le=100000)
    irrigation_source: str | None = PydanticField(default=None, max_length=80)
    water_reliability: str | None = PydanticField(default=None, max_length=80)
    soil_test_status: str | None = PydanticField(default=None, max_length=80)
    primary_soil_type: str | None = PydanticField(default=None, max_length=80)
    equipment_access: list[str] | str | None = None
    labor_availability: str | None = PydanticField(default=None, max_length=80)
    storage_access: str | None = PydanticField(default=None, max_length=80)
    transport_access: str | None = PydanticField(default=None, max_length=80)
    annual_budget_rs: int | None = PydanticField(default=None, ge=0, le=1_000_000_000)
    risk_tolerance: str | None = PydanticField(default=None, max_length=80)
    credit_access: str | None = PydanticField(default=None, max_length=80)
    insurance_status: str | None = PydanticField(default=None, max_length=80)
    organic_preference: bool | None = None
    preferred_mandis: list[str] | str | None = None
    nearest_mandi_km: float | None = PydanticField(default=None, ge=0, le=2000)
    pm_kisan_enrolled: bool | None = None
    pmfby_enrolled: bool | None = None
    kcc_holder: bool | None = None
    soil_health_card: bool | None = None

    @field_validator("equipment_access", "preferred_mandis")
    @staticmethod
    def _bounded_list_or_csv(value):
        if value is None or isinstance(value, str):
            return value
        return [str(item).strip()[:80] for item in value[:20] if str(item).strip()]


# ─── Lifespan ─────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/shutdown events."""
    logger.info("🌾 AgriMesh V4.0 starting up...")
    startup_errors = settings.startup_errors()
    if startup_errors:
        raise RuntimeError("Invalid AgriMesh configuration: " + "; ".join(startup_errors))
    await init_db()
    logger.info("Database initialized.")

    from app.services.evidence import seed_source_registry
    async with async_session_factory() as db:
        await seed_source_registry(db)

    if settings.app_env != "test":
        try:
            from app.services.retrieval import get_embedder, get_reranker

            await asyncio.to_thread(get_embedder)
            await asyncio.to_thread(get_reranker)
            logger.info("Retrieval models preloaded.")
        except Exception as exc:
            logger.error(f"Retrieval model preload failed: {exc}")

    if settings.app_env.lower() == "test":
        yield
        return

    # Start Telegram bot in background (if token configured)
    if settings.telegram_bot_token.strip():
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

# H9: compress JSON dashboard payloads on slow rural links (5-10x typical).
app.add_middleware(GZipMiddleware, minimum_size=1024)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=settings.allowed_origins != ["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router, prefix="/api")


@app.exception_handler(HTTPException)
async def http_exception_handler(_: Request, exc: HTTPException) -> JSONResponse:
    """Keep API failures in one predictable JSON envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "detail": exc.detail,
            "error": {
                "code": "http_error",
                "message": exc.detail,
                "status_code": exc.status_code,
            }
        },
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": "validation_error",
                "message": "Request validation failed",
                "status_code": 422,
                "details": exc.errors(),
            }
        },
    )


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, exc: Exception) -> JSONResponse:
    logger.exception(f"Unhandled API error: {exc}")
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "error": {
                "code": "internal_server_error",
                "message": "Internal server error",
                "status_code": status.HTTP_500_INTERNAL_SERVER_ERROR,
            }
        },
    )


# ─── Production Safety Middleware ─────────────────────────────────────

@app.middleware("http")
async def request_guardrails(request: Request, call_next):
    """Apply low-cost request limits and browser security headers."""
    content_length = request.headers.get("content-length")
    try:
        request_bytes = int(content_length) if content_length else 0
    except ValueError:
        return JSONResponse(
            status_code=status.HTTP_400_BAD_REQUEST,
            content={"detail": "Invalid Content-Length header"},
        )
    if request_bytes > settings.max_request_bytes:
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
    response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Resource-Policy", "same-origin")
    response.headers.setdefault("Cross-Origin-Embedder-Policy", "require-corp")
    response.headers.setdefault(
        "Content-Security-Policy",
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; frame-ancestors 'none'",
    )
    if settings.app_env in {"demo", "production"}:
        response.headers.setdefault(
            "Strict-Transport-Security",
            "max-age=31536000; includeSubDomains",
        )
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


async def require_api_key_or_eval_token(
    request: Request,
    x_agrimesh_api_key: str | None = Header(default=None),
    x_eval_public_token: str | None = Header(default=None, alias="X-Eval-Public-Token"),
) -> bool:
    """Allow public judge eval reads via a narrow token while keeping API auth closed."""
    if settings.eval_public_token:
        provided_eval_token = x_eval_public_token or request.query_params.get("eval_token")
        if provided_eval_token and secrets.compare_digest(
            provided_eval_token,
            settings.eval_public_token,
        ):
            return True
    return await require_api_key(x_agrimesh_api_key)


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


@app.get("/api/farmer-dashboard")
async def current_farmer_dashboard(
    farmer_id: str | None = None,
    phone: str | None = None,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Complete farmer-facing dashboard payload for the PWA."""
    from app.services.farmer_dashboard import get_farmer_dashboard

    if settings.api_key_required and not (farmer_id or phone):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="farmer_id or phone is required when API key protection is enabled",
        )

    data = await get_farmer_dashboard(db, farmer_id=farmer_id, phone=phone)
    if data.get("error") == "farmer_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farmer not found")
    return data


@app.put("/api/farmers/{farmer_id}/profile")
async def update_farmer_profile(
    farmer_id: str,
    request: FarmerProfileUpdate,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Update the farmer profile questionnaire answers."""
    from app.services.farmer_dashboard import upsert_farmer_profile

    result = await upsert_farmer_profile(
        db,
        farmer_id=farmer_id,
        payload=request.model_dump(exclude_unset=True),
    )
    if result.get("error") == "farmer_not_found":
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Farmer not found")
    return result


@app.get("/api/farmers/{farmer_id}/conversation")
async def farmer_conversation(
    farmer_id: str,
    field_id: str | None = None,
    crop_cycle_id: str | None = None,
    limit: int = 12,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Return the durable conversation thread for farmer follow-up continuity."""
    from app.models_memory import ConversationThread, ConversationTurn

    query = select(ConversationThread).where(ConversationThread.farmer_id == farmer_id)
    if field_id:
        query = query.where(ConversationThread.field_id == field_id)
    if crop_cycle_id:
        query = query.where(ConversationThread.crop_cycle_id == crop_cycle_id)
    thread = await db.scalar(query.order_by(desc(ConversationThread.updated_at)).limit(1))
    if not thread:
        return {"thread": None, "turns": []}

    turns_result = await db.execute(
        select(ConversationTurn)
        .where(ConversationTurn.thread_id == thread.id)
        .order_by(desc(ConversationTurn.created_at))
        .limit(min(limit, 50))
    )
    turns = list(reversed(turns_result.scalars().all()))
    return {
        "thread": {
            "id": thread.id,
            "farmer_id": thread.farmer_id,
            "field_id": thread.field_id,
            "crop_cycle_id": thread.crop_cycle_id,
            "title": thread.title,
            "running_summary": thread.running_summary,
            "last_advisory_id": thread.last_advisory_id,
            "turn_count": thread.turn_count,
            "updated_at": thread.updated_at.isoformat() if thread.updated_at else None,
        },
        "turns": [
            {
                "id": turn.id,
                "observation_id": turn.observation_id,
                "advisory_id": turn.advisory_id,
                "user_message": turn.user_message,
                "agent_response": turn.agent_response,
                "detected_followup": turn.detected_followup,
                "risk_level": turn.risk_level,
                "confidence": turn.confidence,
                "retrieval_path": turn.retrieval_path,
                "evidence_article_ids": turn.evidence_article_ids or [],
                "created_at": turn.created_at.isoformat() if turn.created_at else None,
            }
            for turn in turns
        ],
    }


@app.get("/api/advisories/{advisory_id}/impact-network")
async def advisory_impact_network(
    advisory_id: str,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Return action-level impact graph for one advisory, creating it if needed."""
    from app.services.conversation import build_action_impact_network

    impacts = await build_action_impact_network(db, advisory_id)
    await db.commit()
    return {
        "advisory_id": advisory_id,
        "count": len(impacts),
        "impacts": [_serialize_impact(item) for item in impacts],
    }


@app.get("/api/impact-network")
async def latest_impact_network(
    limit: int = 20,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Dashboard feed of latest action impact graph nodes."""
    from app.models_memory import ActionImpact

    result = await db.execute(
        select(ActionImpact).order_by(desc(ActionImpact.created_at)).limit(min(limit, 100))
    )
    impacts = result.scalars().all()
    return {
        "count": len(impacts),
        "impacts": [_serialize_impact(item) for item in impacts],
    }


# ─── Eval Dashboard API ───────────────────────────────────────────────

@app.get("/api/eval/latest")
async def latest_eval(_: bool = Depends(require_api_key_or_eval_token)):
    """Return latest eval run results."""
    eval_path = Path(settings.eval_dir) / "latest_results.json"
    if not eval_path.exists():
        return {"status": "no_eval_yet", "message": "Run make eval to generate results"}
    import json
    return json.loads(eval_path.read_text(encoding="utf-8"))


@app.get("/api/eval/history")
async def eval_history(_: bool = Depends(require_api_key_or_eval_token)):
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
    from app.models_memory import (
        ActionImpact,
        ConversationThread,
        MemoryAtom,
        MemorySummary,
        SourceDocument,
    )
    memory_atom_count = (await db.execute(select(func.count(MemoryAtom.id)))).scalar()
    memory_summary_count = (await db.execute(select(func.count(MemorySummary.id)))).scalar()
    source_count = (await db.execute(select(func.count(SourceDocument.id)))).scalar()
    conversation_count = (await db.execute(select(func.count(ConversationThread.id)))).scalar()
    impact_count = (await db.execute(select(func.count(ActionImpact.id)))).scalar()

    return {
        "farmers": farmer_count,
        "advisories": advisory_count,
        "pending_clusters": cluster_count,
        "wiki_articles": wiki_count,
        "memory_atoms": memory_atom_count,
        "memory_summaries": memory_summary_count,
        "sources": source_count,
        "conversations": conversation_count,
        "action_impacts": impact_count,
        "model": settings.ollama_model,
    }


@app.get("/api/models")
async def model_options(_: bool = Depends(require_api_key)):
    """Return configured Gemma/Ollama model choices for the frontend model toggle."""
    return {
        "current": settings.ollama_model,
        "fallback": settings.ollama_fallback_model,
        "options": [
            {
                "id": settings.ollama_model,
                "label": settings.ollama_model.replace(":", " "),
                "role": "primary",
            },
            {
                "id": settings.ollama_fallback_model,
                "label": settings.ollama_fallback_model.replace(":", " "),
                "role": "fallback",
            },
        ],
        "grammar_decoding": settings.use_grammar_decoding,
        "timeout_seconds": settings.ollama_timeout_seconds,
    }


@app.get("/api/weather/forecast")
async def weather_forecast(
    field_id: str | None = None,
    days: int = 5,
    _: bool = Depends(require_api_key),
):
    """Return seeded weather forecast data in the same shape used by the agent tools."""
    from app.services.weather import get_forecast, get_historical_weather

    bounded_days = min(max(days, 1), 10)
    forecast = await get_forecast(field_id=field_id, days=bounded_days)
    history = await get_historical_weather(field_id=field_id, days=min(bounded_days, 7))
    return {"forecast": forecast, "history": history}


@app.get("/api/market-prices")
async def market_prices(
    crop: str = "rice",
    district: str | None = None,
    days: int = 7,
    _: bool = Depends(require_api_key),
):
    """Return seeded mandi price data for a crop/district."""
    from app.services.mandi import get_mandi_prices, get_msp

    bounded_days = min(max(days, 1), 30)
    prices = await get_mandi_prices(crop=crop, district=district, days=bounded_days)
    msp = await get_msp(crop=crop)
    return {"crop": crop, "district": district, "days": bounded_days, "prices": prices, "msp": msp}


@app.get("/api/ai/showcase")
async def ai_showcase(
    farmer_id: str | None = None,
    limit: int = 5,
    db: AsyncSession = Depends(get_db),
    _: bool = Depends(require_api_key),
):
    """Return latest advisory, vision, tool-call, citation, and model metadata for demo pages."""
    from app.models import Observation
    from app.models_memory import SourceCitation, SourceDocument

    bounded_limit = min(max(limit, 1), 20)
    query = select(Advisory).order_by(desc(Advisory.created_at)).limit(bounded_limit)
    if farmer_id:
        query = query.where(Advisory.farmer_id == farmer_id)
    result = await db.execute(query)
    advisories = result.scalars().all()
    latest = advisories[0] if advisories else None

    observation = None
    citations = []
    if latest:
        observation = await db.scalar(select(Observation).where(Observation.id == latest.observation_id))
        citation_result = await db.execute(
            select(SourceCitation, SourceDocument)
            .join(SourceDocument, SourceDocument.id == SourceCitation.source_document_id)
            .where(SourceCitation.advisory_id == latest.id)
            .order_by(SourceCitation.created_at.desc())
            .limit(10)
        )
        citations = [
            {
                "id": citation.id,
                "source_name": source.source_name,
                "source_type": source.source_type,
                "url": source.url,
                "trust_level": source.trust_level,
                "is_official": source.is_official,
                "relevance_score": citation.relevance_score,
                "context": citation.citation_context,
                "snapshot": citation.evidence_snapshot,
            }
            for citation, source in citation_result.all()
        ]

    tool_calls = []
    if latest:
        for tool_name, payload in [
            ("get_forecast", latest.weather_data),
            ("get_mandi_prices", latest.mandi_data),
            ("match_schemes", latest.scheme_data),
        ]:
            if payload:
                tool_calls.append({"tool_name": tool_name, "status": "used", "result_preview": payload})

    reasoning_trace = []
    if latest:
        reasoning_trace = [
            {
                "step": "Evidence retrieval",
                "summary": f"{len(latest.evidence_article_ids or [])} evidence articles selected via {latest.retrieval_path or 'unknown'} retrieval.",
            },
            {
                "step": "Tool grounding",
                "summary": f"{len(tool_calls)} agent tools contributed live or seeded context.",
            },
            {
                "step": "Safety verification",
                "summary": "Verifier passed all checks." if latest.verifier_report and latest.verifier_report.passes_all else "Verifier required a conservative fallback or has no report yet.",
            },
        ]

    return {
        "models": {
            "current": settings.ollama_model,
            "fallback": settings.ollama_fallback_model,
            "options": [settings.ollama_model, settings.ollama_fallback_model],
        },
        "latest_advisory": _serialize_advisory_for_showcase(latest) if latest else None,
        "vision": {
            "image_path": observation.image_path if observation else None,
            "analysis": observation.vision_analysis if observation else None,
            "confidence": observation.vision_confidence if observation else None,
        } if observation else None,
        "reasoning_trace": reasoning_trace,
        "tool_calls": tool_calls,
        "citations": citations,
        "history": [_serialize_advisory_for_showcase(item) for item in advisories],
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

    from app.models_memory import SourceDocument

    result = await db.execute(select(SourceDocument).order_by(SourceDocument.trust_level, SourceDocument.source_name))
    sources = result.scalars().all()
    now = utc_now()
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


def _serialize_impact(item) -> dict:
    """Serialize an ActionImpact row for API/PWA consumers."""
    return {
        "id": item.id,
        "advisory_id": item.advisory_id,
        "farmer_id": item.farmer_id,
        "field_id": item.field_id,
        "crop_cycle_id": item.crop_cycle_id,
        "action_index": item.action_index,
        "action_text": item.action_text,
        "impact_level": item.impact_level,
        "expected_result": item.expected_result,
        "time_horizon": item.time_horizon,
        "dependencies": item.dependencies or [],
        "risks": item.risks or [],
        "metrics_delta": item.metrics_delta or {},
        "affects_previous_suggestions": item.affects_previous_suggestions or [],
        "evidence_article_ids": item.evidence_article_ids or [],
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


def _serialize_advisory_for_showcase(item: Advisory | None) -> dict | None:
    if not item:
        return None
    return {
        "id": item.id,
        "risk_level": item.risk_level,
        "confidence": item.confidence,
        "contextualization": item.contextualization,
        "actions_text": item.actions_text or [],
        "warnings_text": item.warnings_text or [],
        "thinking_enabled": item.thinking_enabled,
        "model_used": item.model_used,
        "retrieval_path": item.retrieval_path,
        "latency_ms": item.latency_ms,
        "evidence_article_ids": item.evidence_article_ids or [],
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }


# ─── Static (PWA) ─────────────────────────────────────────────────────

pwa_dir = Path(__file__).parent.parent / "pwa" / "dist"
if pwa_dir.exists():
    from fastapi.responses import FileResponse

    _pwa_index = pwa_dir / "index.html"
    app.mount("/assets", StaticFiles(directory=str(pwa_dir / "assets")), name="pwa-assets")

    @app.get("/{full_path:path}")
    async def serve_spa(full_path: str):
        # Serve actual files (manifest, icons, etc.) if they exist
        candidate = pwa_dir / full_path
        if full_path and candidate.is_file() and candidate.resolve().is_relative_to(pwa_dir.resolve()):
            return FileResponse(candidate)
        # Everything else falls through to index.html for the SPA router
        return FileResponse(_pwa_index)
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
