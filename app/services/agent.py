"""
AgriMesh V4.0 — Agent Orchestrator
Two-step hybrid loop:
  Step 1: ReAct-style planning (Gemma 4 + thinking ON + MCP tools)
  Step 2: Template selection (Gemma 4 + thinking OFF + grammar-constrained)

Coordinates: OllamaClient, RetrievalService, VerifierService, MCP tools.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, ClassVar

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Advisory,
    Farmer,
    Field,
)
from app.models import (
    VerifierReport as VerifierReportModel,
)
from app.services.retrieval import speculative_retrieve
from app.services.verifier import (
    EvidenceBundle,
    Recommendation,
    VerifierReport,
    get_verifier,
)
from app.utils.ollama_client import get_ollama
from app.utils.safety import SAFE_FALLBACK_HI
from app.utils.time import utc_now


@dataclass
class AgentContext:
    """All context the agent needs to process one farmer query."""
    farmer_id: str
    message: str
    language: str  # hi, en, mixed
    crop_name: str | None = None
    crop_stage: str | None = None
    field_id: str | None = None
    crop_cycle_id: str | None = None
    observation_id: str | None = None
    image_path: str | None = None
    is_followup: bool = False
    previous_advisory_id: str | None = None
    # Populated from DB at process() entry — real value, never a placeholder.
    land_owned_acres: float | None = None


@dataclass
class AgentResponse:
    """Complete agent response after verification."""
    advisory_id: str | None
    display_text: str  # What the farmer sees in Telegram/PWA
    risk_level: str
    confidence: str
    evidence_cards: list[dict]
    verifier_report: VerifierReport | None
    latency_ms: int
    model_used: str
    retrieval_path: str
    thinking_enabled: bool


# ─── Agent Orchestrator ───────────────────────────────────────────────

class AgentOrchestrator:

    def __init__(self):
        self.llm = get_ollama()
        self.verifier = get_verifier()

    async def process(
        self,
        db: AsyncSession,
        ctx: AgentContext,
    ) -> AgentResponse:
        """
        Full agent pipeline for one farmer message.

        Flow:
          1. Intent classification (thinking OFF)
          2. Speculative retrieval (parallel)
          3. ReAct planning → MCP tool calls (thinking ON)
          4. Template selection (thinking OFF)
          5. Verifier check (4 lines)
          6. Return safe response
        """
        t0 = time.perf_counter()
        evidence = EvidenceBundle()
        thinking_enabled = False
        detected_followup = ctx.is_followup

        # Hydrate land_owned_acres from the real Field row (no placeholder).
        if ctx.land_owned_acres is None and ctx.field_id:
            try:
                from sqlalchemy import select as _sa_select

                from app.models import Field as _FieldModel
                _res = await db.execute(_sa_select(_FieldModel.area_acres).where(_FieldModel.id == ctx.field_id))
                _acres = _res.scalar_one_or_none()
                if isinstance(_acres, int | float):
                    ctx.land_owned_acres = float(_acres)
            except Exception as exc:
                logger.warning(f"Land acres hydration failed: {exc}")

        # ── Step 0: Vision analysis (if photo provided) ──────────
        vision_result = None
        if ctx.image_path:
            try:
                vision_result = await self.llm.analyze_crop_photo(
                    ctx.image_path, ctx.message
                )
                logger.info(f"Vision analysis: {str(vision_result)[:200]}")
            except Exception as exc:
                logger.error(f"Vision analysis failed: {exc}")

        # Load conversation context early so the intent classifier can
        # resolve follow-up references ("I did what you said") against the
        # most recent advisory. The same string is reused at Step 6 so this
        # is not a duplicate DB query.
        conversation_context = await self._load_conversation_context(db, ctx)

        # ── Step 1: Intent classification (LLM-decided entities) ─
        intent: dict = {}
        llm_crop_name = ""
        llm_crop_stage = ""
        llm_state = ""
        llm_topic_tags: list[str] = []
        llm_is_followup = False
        llm_referenced_action = ""
        llm_referenced_problem = ""
        try:
            intent_result = await self.llm.classify_intent(
                ctx.message,
                ctx.language,
                conversation_context=conversation_context,
            )
            intent = intent_result.get("parsed", {}) or {}
            needs_retrieval = intent.get("needs_retrieval", True)
            needs_tools = intent.get("needs_tool_call", False)
            # Hard override: factual lookup intents must hit retrieval + tools.
            # The grammar-constrained classifier sometimes returns the right
            # intent but with both flags off, which leaves the agent with no
            # data and produces an irrelevant templated reply.
            if intent.get("intent") in {"market_query", "scheme_query", "weather_query", "finance_query"}:
                needs_retrieval = True
                needs_tools = True
            llm_crop_name = (intent.get("crop_name") or "").strip()
            llm_crop_stage = (intent.get("crop_stage") or "").strip()
            llm_state = (intent.get("state_or_region") or "").strip()
            llm_topic_tags = [t for t in (intent.get("topic_tags") or []) if t]
            llm_is_followup = bool(intent.get("is_followup", False))
            llm_referenced_action = (intent.get("referenced_action") or "").strip()
            llm_referenced_problem = (intent.get("referenced_problem") or "").strip()
            logger.info(
                f"Intent: {intent.get('intent')}, retrieval={needs_retrieval}, tools={needs_tools}, "
                f"crop='{llm_crop_name}', stage='{llm_crop_stage}', region='{llm_state}', "
                f"tags={llm_topic_tags}, followup={llm_is_followup}, "
                f"ref_action='{llm_referenced_action}', ref_problem='{llm_referenced_problem}'"
            )
        except Exception as exc:
            logger.error(f"Intent classification failed: {exc}")
            needs_retrieval = True
            needs_tools = False

        # Propagate LLM-decided entities onto ctx so downstream steps (tool
        # adapters, retrieval filters, memory) read them without a second LLM call.
        if llm_crop_name and not ctx.crop_name:
            ctx.crop_name = llm_crop_name
        if llm_crop_stage and not ctx.crop_stage:
            ctx.crop_stage = llm_crop_stage

        # ── Step 1.5: Durable conversation routing ───────────────
        previous_article_ids: list[str] = []
        routed_thread_id: str | None = None
        try:
            from app.services.conversation import previous_evidence_article_ids, route_to_thread

            detected_followup = detected_followup or llm_is_followup
            previous_article_ids = await previous_evidence_article_ids(
                db,
                farmer_id=ctx.farmer_id,
                field_id=ctx.field_id,
                crop_cycle_id=ctx.crop_cycle_id,
            )
            routed_thread = await route_to_thread(
                db,
                farmer_id=ctx.farmer_id,
                field_id=ctx.field_id,
                crop_cycle_id=ctx.crop_cycle_id,
                intent_crop_name=llm_crop_name or None,
            )
            routed_thread_id = routed_thread.id
        except Exception as exc:
            logger.warning(f"Conversation continuity lookup failed: {exc}")

        # ── Step 2: Speculative retrieval (fire in parallel) ─────
        retrieval_task = None
        if needs_retrieval:
            topic_tags = llm_topic_tags or ["general"]
            retrieval_task = await speculative_retrieve(
                db,
                query=ctx.message,
                crop_name=ctx.crop_name,
                stage=ctx.crop_stage,
                topic_tags=topic_tags,
                is_followup=detected_followup,
                previous_article_ids=previous_article_ids,
            )

        # ── Step 3: ReAct planning + MCP tool calls ──────────────
        tool_results = {}
        if needs_tools:
            try:
                thinking_enabled = True
                plan_result = await self.llm.plan_tools(ctx.message, {
                    "farmer_id": ctx.farmer_id,
                    "crop": ctx.crop_name,
                    "stage": ctx.crop_stage,
                    "field_id": ctx.field_id,
                })
                plan = plan_result.get("parsed", {})
                tool_calls = plan.get("tool_calls", [])

                # Execute MCP tool calls in parallel. Keep a parallel list of
                # the executable calls so results map back by index — using
                # the unfiltered ``tool_calls`` here mis-indexes whenever the
                # planner emits a "no_tool" entry and silently drops real
                # tool results (e.g. weather never reaches evidence).
                tool_tasks = []
                executable_calls = []
                for tc in tool_calls:
                    tool_name = tc.get("tool_name", "")
                    params = tc.get("parameters", {})
                    if tool_name and tool_name != "no_tool":
                        tool_tasks.append(self._execute_tool(tool_name, params, ctx))
                        executable_calls.append(tc)

                if tool_tasks:
                    tool_results_list = await asyncio.gather(*tool_tasks, return_exceptions=True)
                    for i, result in enumerate(tool_results_list):
                        called_name = executable_calls[i].get("tool_name")
                        if isinstance(result, Exception):
                            logger.error(f"Tool {called_name} failed: {result}")
                        else:
                            tool_results[called_name] = result
            except Exception as exc:
                logger.error(f"ReAct planning failed: {exc}. Proceeding without tools.")
                needs_tools = False

        # ── Step 4: Await retrieval (or cancel if not needed) ────
        retrieval_result = None
        if retrieval_task:
            if needs_retrieval:
                try:
                    retrieval_result = await retrieval_task
                except Exception as exc:
                    logger.error(f"Retrieval failed: {exc}")
            else:
                retrieval_task.cancel()

        # ── Step 5: Build evidence bundle ────────────────────────
        if retrieval_result and retrieval_result.get("articles"):
            evidence.wiki_articles = retrieval_result["articles"]
        if "get_forecast" in tool_results or "get_historical_weather" in tool_results:
            evidence.weather_data = tool_results.get("get_forecast") or tool_results.get("get_historical_weather")
        if "get_mandi_prices" in tool_results:
            evidence.mandi_data = tool_results.get("get_mandi_prices")
        if "get_msp" in tool_results:
            msp_result = tool_results.get("get_msp") or {}
            if evidence.mandi_data is None:
                evidence.mandi_data = {}
            if msp_result.get("msp_rs_per_quintal") and not evidence.mandi_data.get("msp"):
                evidence.mandi_data["msp"] = msp_result.get("msp_rs_per_quintal")
            if msp_result.get("crop") and not evidence.mandi_data.get("crop"):
                evidence.mandi_data["crop"] = msp_result.get("crop")
        if "match_schemes" in tool_results:
            evidence.scheme_data = tool_results.get("match_schemes")

        if needs_retrieval and not evidence.weather_data and (ctx.crop_name or ctx.field_id):
            try:
                from app.services.weather import get_forecast, get_historical_weather

                forecast, history = await asyncio.gather(
                    get_forecast(field_id=ctx.field_id, days=3),
                    get_historical_weather(field_id=ctx.field_id, days=3),
                    return_exceptions=True,
                )
                evidence.weather_data = {
                    "forecast": None if isinstance(forecast, Exception) else forecast,
                    "history": None if isinstance(history, Exception) else history,
                    "use_as": "weather factor context for agronomic risk, spray timing, disease pressure, water stress, and storage decisions",
                }
            except Exception as exc:
                logger.warning(f"automatic weather context skipped: {exc}")

        # Closed-loop market decision: selling/procurement intent should not
        # stop at raw mandi prices. Route to the deterministic sell advisor so
        # the farmer gets sell/wait/store guidance grounded in MSP + storage.
        if self._is_sell_intent(ctx.message, intent):
            try:
                farmer = await db.scalar(select(Farmer).where(Farmer.id == ctx.farmer_id))
                from app.services.market_intel import sell_decision_advisor

                tool_results["sell_decision"] = await sell_decision_advisor(
                    crop=ctx.crop_name or llm_crop_name or "rice",
                    district=getattr(farmer, "district", None) or "Munger",
                )
            except Exception as exc:
                logger.warning(f"sell decision advisor skipped: {exc}")

        if self._is_cluster_intent(ctx.message):
            try:
                farmer = await db.scalar(select(Farmer).where(Farmer.id == ctx.farmer_id))
                from app.services.cluster_intel import (
                    district_pest_pressure,
                    district_stage_distribution,
                )

                district = getattr(farmer, "district", None) or ""
                crop = ctx.crop_name or llm_crop_name or "rice"
                tool_results["cluster_intel"] = {
                    "stage_distribution": await district_stage_distribution(db, crop, district),
                    "pest_pressure": await district_pest_pressure(db, crop, district),
                    "district": district,
                    "crop": crop,
                }
            except Exception as exc:
                logger.warning(f"cluster intel skipped: {exc}")

        # Universal-KB: synchronous, in-memory, sub-ms per call. No need
        # to fire it in parallel — just retrieve once crop/state/stage are
        # known. Falls through silently when the loader has no rows.
        try:
            from app.services import universal_kb
            state_hint = None
            try:
                farmer_row = await db.scalar(select(Farmer).where(Farmer.id == ctx.farmer_id))
                state_hint = getattr(farmer_row, "state", None) or "Bihar"
            except Exception:
                pass
            should_load_kb = bool(ctx.crop_name) or intent.get("intent") in {"scheme_query", "market_query"}
            intent_name = intent.get("intent")
            # Scope KB doc-types to the intent so pest/disease answers don't
            # get MSP/scheme cards stuffed into the LLM prompt (which the model
            # then hallucinates a bridge to — "I'll give you MSP since you
            # mentioned rice"). Allow everything for the explicit financial/
            # scheme intents that need that data.
            if intent_name in {"market_query", "scheme_query", "finance_query"}:
                kb_allowed: set[str] | None = None
            else:
                kb_allowed = {"playbook", "playbook_stage", "official_manual", "common_issue_memory", "encyclopedia"}
            evidence.universal_kb_docs = (
                universal_kb.retrieve(
                    crop=ctx.crop_name,
                    state=state_hint,
                    stage=ctx.crop_stage,
                    query=ctx.message,
                    allowed_types=kb_allowed,
                ) if should_load_kb else []
            ) or []
            if (
                evidence.universal_kb_docs
                and not evidence.wiki_articles
                and not self._is_sell_intent(ctx.message, intent)
                and not self._is_cluster_intent(ctx.message)
            ):
                evidence.wiki_articles = self._kb_docs_as_articles(evidence.universal_kb_docs)
        except Exception as exc:
            logger.debug(f"universal_kb retrieval skipped: {exc}")

        # Load memory context (conversation_context was loaded before Step 1
        # so the intent classifier could resolve follow-up references; reuse
        # it here instead of re-querying).
        evidence.memory_context = await self._load_memory_context(db, ctx)
        profile_context = await self._load_personal_profile_context(db, ctx)
        # Factual lookups (MSP, schemes, weather, finance) are stateless. Past
        # turns about pests or diseases poison template selection and let the
        # LLM drag irrelevant prior context into a price/scheme answer.
        is_factual = intent.get("intent") in {"market_query", "scheme_query", "weather_query", "finance_query"}
        ctx_parts = [profile_context, evidence.memory_context] if is_factual else [profile_context, conversation_context, evidence.memory_context]
        evidence.memory_context = "\n".join(part for part in ctx_parts if part)

        # Load NDVI satellite data (§10.3)
        evidence.ndvi_data = await self._load_ndvi_data(db, ctx)

        # ── Step 6: Template selection ───────────────────────────
        # Factual intents (market/scheme/weather/finance) must answer from
        # tool data, not from disease/playbook wiki articles. Suppress wiki
        # articles so the tool-only branch fires and renders the mandi/MSP/
        # scheme/weather block as the primary response.
        if is_factual:
            evidence.wiki_articles = []
        selection_result = None
        if evidence.wiki_articles:
            selection_result = await self.llm.select_template(
                ctx.message,
                evidence.wiki_articles,
                evidence.memory_context,
                evidence.universal_kb_docs,
            )
        else:
            # No wiki evidence. If tools returned something (weather, mandi,
            # scheme), synthesize a tool-grounded response instead of falling
            # through to the disease-clarification template — that template
            # is wrong for every non-disease intent.
            toolworthy = intent.get("intent") in {"weather_query", "market_query", "finance_query", "scheme_query"}
            tool_response = (
                self._build_tool_only_response(ctx, tool_results, evidence, intent)
                if toolworthy or self._is_sell_intent(ctx.message, intent) or self._is_cluster_intent(ctx.message)
                else None
            )
            if tool_response is not None:
                return await self._tool_only_response(
                    db, ctx, t0, tool_response, tool_results
                )
            # No evidence + no tools. If this is a follow-up turn ("I did
            # what you said, now what?"), don't show a generic clarification
            # — produce a deterministic monitor/continue advisory grounded
            # in the prior action the farmer references. Without this,
            # follow-up turns score 0 on evidence_ok because the
            # clarification template has no continuity keywords.
            if detected_followup:
                fu_response = self._build_followup_response(
                    ctx,
                    conversation_context,
                    referenced_action=llm_referenced_action,
                    referenced_problem=llm_referenced_problem,
                )
                if fu_response is not None:
                    return await self._tool_only_response(
                        db, ctx, t0, fu_response, tool_results
                    )
            # No evidence at all — fall back to conservative clarification.
            return await self._no_evidence_response(db, ctx, t0)

        parsed = selection_result.get("parsed", {})
        recommendation = Recommendation(
            risk_level=parsed.get("risk_level", "WATCH"),
            confidence=parsed.get("confidence", "MEDIUM"),
            selected_action_indices=parsed.get("selected_action_indices", []),
            selected_warning_indices=parsed.get("selected_warning_indices", []),
            contextualization=parsed.get("contextualization", ""),
            memory_reference=parsed.get("memory_reference", ""),
            should_escalate=parsed.get("should_escalate_to_extension_worker", False),
            raw_json=parsed,
        )

        # Resolve indices to text
        recommendation.actions_text = self._resolve_actions(
            recommendation.selected_action_indices, evidence.wiki_articles
        )
        recommendation.warnings_text = self._resolve_warnings(
            recommendation.selected_warning_indices, evidence.wiki_articles
        )

        # ── Step 7: Verifier ─────────────────────────────────────
        verifier_report, safe_fallback = await self.verifier.verify(
            recommendation, evidence
        )

        # ── Step 8: Build display text ───────────────────────────
        if verifier_report.passes_all:
            display_text = await self._build_advisory_display(db, ctx, recommendation, evidence)
        else:
            display_text = safe_fallback or SAFE_FALLBACK_HI
            logger.warning("Verifier failed — using safe fallback")
            # Replace the rejected recommendation with the fallback so the
            # persisted Advisory + ActionImpact records match what the farmer
            # actually saw. Downgrade confidence to LOW (verifier rejected it),
            # drop the unsafe wiki-backed actions, and store the fallback text
            # in contextualization. raw_json keeps the original LLM output for
            # audit/debugging.
            recommendation = Recommendation(
                risk_level=recommendation.risk_level,
                confidence="LOW",
                selected_action_indices=[],
                selected_warning_indices=[],
                contextualization=display_text,
                actions_text=[],
                warnings_text=[],
                memory_reference=recommendation.memory_reference,
                should_escalate=recommendation.should_escalate,
                raw_json=recommendation.raw_json,
            )

        # ── Step 9: Persist advisory ─────────────────────────────
        advisory_id = await self._persist_advisory(
            db, ctx, recommendation, evidence, verifier_report,
            selection_result.get("model_used", ""),
            selection_result.get("latency_ms", 0),
            retrieval_result.get("path", "fast") if retrieval_result else "none",
            thinking_enabled,
        )

        if advisory_id:
            await self._record_conversation_and_impacts(
                db=db,
                ctx=ctx,
                advisory_id=advisory_id,
                display_text=display_text,
                recommendation=recommendation,
                evidence=evidence,
                retrieval_path=retrieval_result.get("path", "none") if retrieval_result else "none",
                detected_followup=detected_followup,
                thread_id=routed_thread_id,
            )

        latency_ms = int((time.perf_counter() - t0) * 1000)

        # Build evidence cards for display
        evidence_cards = self._build_evidence_cards(evidence, vision_result)

        return AgentResponse(
            advisory_id=advisory_id,
            display_text=display_text,
            risk_level=recommendation.risk_level,
            confidence=recommendation.confidence,
            evidence_cards=evidence_cards,
            verifier_report=verifier_report,
            latency_ms=latency_ms,
            model_used=selection_result.get("model_used", "") if selection_result else "",
            retrieval_path=retrieval_result.get("path", "none") if retrieval_result else "none",
            thinking_enabled=thinking_enabled,
        )

    # ── Helpers ──────────────────────────────────────────────────

    async def _execute_tool(
        self,
        tool_name: str,
        params: dict,
        ctx: AgentContext | None = None,
    ) -> Any:
        """Execute an MCP tool call.

        Planner-emitted kwargs don't always match real signatures (e.g. it
        sends ``location`` for weather or omits required ``crop`` for mandi).
        Adapt + inject context defaults so the tool actually runs instead of
        crashing the whole ReAct step.
        """
        # Import tool functions lazily
        from app.services.mandi import get_mandi_prices, get_msp
        from app.services.scheme import match_schemes
        from app.services.weather import get_forecast, get_historical_weather

        tool_map = {
            "get_forecast": get_forecast,
            "get_historical_weather": get_historical_weather,
            "get_mandi_prices": get_mandi_prices,
            "get_msp": get_msp,
            "match_schemes": match_schemes,
        }

        fn = tool_map.get(tool_name)
        if not fn:
            return {"error": f"Unknown tool: {tool_name}"}

        adapted = self._adapt_tool_params(tool_name, dict(params or {}), ctx)
        try:
            return await fn(**adapted)
        except TypeError as exc:
            # Planner emitted unknown kwargs — strip to known params and retry.
            logger.warning(
                f"Tool {tool_name} TypeError ({exc}); retrying with filtered kwargs"
            )
            import inspect
            sig = inspect.signature(fn)
            allowed = {k: v for k, v in adapted.items() if k in sig.parameters}
            return await fn(**allowed)

    @staticmethod
    def _adapt_tool_params(
        tool_name: str,
        params: dict,
        ctx: AgentContext | None,
    ) -> dict:
        """Map planner kwargs to real tool signatures + inject ctx defaults."""
        # Common aliases coming from the LLM planner
        if "location" in params and "field_id" not in params:
            # Drop location string — field_id is the real key, ctx supplies it
            params.pop("location", None)

        # Crop name comes from LLM intent extraction (propagated onto ctx) — no
        # keyword/placeholder fallback. If the LLM did not surface a crop, we
        # only inject `crop` for tools that strictly require it (mandi/MSP/scheme)
        # and otherwise leave it unset for the tool to error or skip cleanly.
        crop_default = ctx.crop_name if ctx and ctx.crop_name else None
        field_default = ctx.field_id if ctx else None
        farmer_id = ctx.farmer_id if ctx else None

        if tool_name in ("get_forecast", "get_historical_weather"):
            params.setdefault("field_id", field_default)
            if "days_ahead" in params and "days" not in params:
                params["days"] = params.pop("days_ahead")
        elif tool_name == "get_mandi_prices":
            if crop_default:
                params.setdefault("crop", crop_default)
            for alt in ("market", "mandi", "city"):
                if alt in params and "district" not in params:
                    params["district"] = params.pop(alt)
                else:
                    params.pop(alt, None)
        elif tool_name == "get_msp":
            if crop_default:
                params.setdefault("crop", crop_default)
        elif tool_name == "match_schemes":
            if crop_default:
                params.setdefault("crop", crop_default)
            # Hydrate farmer_profile from real farmer/field rows. land_owned_acres
            # was loaded from Field.area_acres at process() entry — no 2.5/'small'
            # placeholder. If absent, the scheme service falls back to its own
            # default rather than us lying about smallholder status.
            fp = params.get("farmer_profile") or {}
            if farmer_id and "farmer_id" not in fp:
                fp["farmer_id"] = farmer_id
            acres = ctx.land_owned_acres if ctx else None
            if isinstance(acres, int | float) and "land_owned_acres" not in fp:
                fp["land_owned_acres"] = float(acres)
            params["farmer_profile"] = fp
            if "field" not in params and field_default:
                field_payload = {"field_id": field_default}
                if isinstance(acres, int | float):
                    field_payload["area_acres"] = float(acres)
                params["field"] = field_payload

        return params

    async def _load_memory_context(self, db: AsyncSession, ctx: AgentContext) -> str:
        """Load farmer's memory via the Living Memory system (semantic top-k, filtered).

        Also pulls anonymized peer-farmer atoms from the same district when the
        k-anonymity floor is met (M4 cross-farmer learning).
        """
        from sqlalchemy import select

        from app.models import Farmer
        from app.services.memory import (
            retrieve_memory_context,
            retrieve_similar_farm_context,
        )

        # Do not pin risk_type to "disease" here: it filtered out pest,
        # weather, nutrient, and outcome atoms that share the same field
        # scope. The function already ranks by temporal-decay-weighted
        # confidence, so dropping the substring filter is safe — the
        # template selector picks what to surface.
        memory_atoms = await retrieve_memory_context(
            db=db,
            farmer_id=ctx.farmer_id,
            field_id=ctx.field_id,
            crop_cycle_id=ctx.crop_cycle_id,
            crop_name=ctx.crop_name,
            crop_stage=ctx.crop_stage,
            top_k=8,
        )

        peer_atoms: list[dict] = []
        if ctx.crop_name:
            farmer = await db.scalar(select(Farmer).where(Farmer.id == ctx.farmer_id))
            district = farmer.district if farmer else None
            if district:
                try:
                    peer_atoms = await retrieve_similar_farm_context(
                        db=db,
                        farmer_id=ctx.farmer_id,
                        crop_name=ctx.crop_name,
                        district=district,
                        top_k=5,
                    )
                except Exception:
                    peer_atoms = []

        if not memory_atoms and not peer_atoms:
            return ""

        lines: list[str] = []
        if memory_atoms:
            lines.append("Field memory (semantic retrieval):")
            for mem in memory_atoms[:8]:
                event_info = f" [{mem.get('event_at', '?')[:10]}]" if mem.get('event_at') else ""
                lines.append(f"  - [{mem.get('atom_type', 'event')}]{event_info}: {mem.get('summary', '')[:200]}")
                if mem.get("type") == "village_summary":
                    lines.append(f"    Village pattern: {mem.get('patterns', [])}")

        if peer_atoms:
            lines.append(
                f"Nearby farms (anonymized, same district, same crop, n={len(peer_atoms)}):"
            )
            for mem in peer_atoms[:5]:
                event_info = f" [{mem.get('event_at', '?')[:10]}]" if mem.get('event_at') else ""
                lines.append(
                    f"  - [{mem.get('atom_type', 'event')}]{event_info}: {mem.get('summary', '')[:200]}"
                )
        return "\n".join(lines)

    async def _load_personal_profile_context(self, db: AsyncSession, ctx: AgentContext) -> str:
        """Load stable farmer/field/crop facts for personalization."""
        from sqlalchemy import select

        from app.models import CropCycle, Farmer, Field
        from app.models_memory import FarmerProfile

        farmer = await db.scalar(select(Farmer).where(Farmer.id == ctx.farmer_id))
        field = await db.scalar(select(Field).where(Field.id == ctx.field_id)) if ctx.field_id else None
        cycle = (
            await db.scalar(select(CropCycle).where(CropCycle.field_id == ctx.field_id, CropCycle.is_active))
            if ctx.field_id
            else None
        )
        profile = await db.scalar(select(FarmerProfile).where(FarmerProfile.farmer_id == ctx.farmer_id))

        lines = ["Personal farm context:"]
        if farmer:
            lines.append(
                f"  Farmer region: village={farmer.village or '?'}, tehsil={farmer.tehsil or '?'}, "
                f"district={farmer.district or '?'}, language={farmer.preferred_language or ctx.language}"
            )
        if field:
            lines.append(
                f"  Active field: {field.name or field.id}, area={field.area_acres or '?'} acres, "
                f"soil={field.soil_type or '?'}, irrigation={field.irrigation_type or '?'}"
            )
        if cycle:
            lines.append(
                f"  Active crop: {cycle.crop_name}, variety={cycle.variety or '?'}, "
                f"stage={cycle.current_stage or ctx.crop_stage or '?'}"
            )
        if profile:
            lines.append(
                "  Constraints: "
                f"water={profile.water_reliability or '?'}, budget={profile.annual_budget_rs or '?'}, "
                f"risk={profile.risk_tolerance or '?'}, organic={profile.organic_preference}"
            )
        return "\n".join(lines) if len(lines) > 1 else ""

    async def _load_conversation_context(self, db: AsyncSession, ctx: AgentContext) -> str:
        """Load durable previous exchanges for follow-up continuity."""
        from app.services.conversation import build_conversation_context

        return await build_conversation_context(
            db=db,
            farmer_id=ctx.farmer_id,
            field_id=ctx.field_id,
            crop_cycle_id=ctx.crop_cycle_id,
            max_turns=4,
        )

    async def _load_ndvi_data(self, db: AsyncSession, ctx: AgentContext) -> dict | None:
        """§10.3: Load satellite NDVI data for the farmer's field."""
        from sqlalchemy import desc, select

        from app.models import SatelliteNDVI

        field_id = ctx.field_id
        if not field_id:
            # Find farmer's first field
            result = await db.execute(
                select(Field).where(Field.farmer_id == ctx.farmer_id).limit(1)
            )
            field = result.scalar_one_or_none()
            field_id = field.id if field else None

        if not field_id:
            return None

        result = await db.execute(
            select(SatelliteNDVI)
            .where(SatelliteNDVI.field_id == field_id)
            .order_by(desc(SatelliteNDVI.date))
            .limit(6)
        )
        ndvi_records = result.scalars().all()
        if not ndvi_records:
            return None

        values = [r.ndvi_value for r in reversed(ndvi_records)]
        dates = [r.date.strftime("%Y-%m-%d") if r.date else "?" for r in reversed(ndvi_records)]

        # Calculate trend
        if len(values) >= 2:
            change = values[-1] - values[0]
            trend = "declining" if change < -0.03 else "improving" if change > 0.03 else "stable"
        else:
            trend = "insufficient_data"

        return {
            "field_id": field_id,
            "latest_ndvi": values[-1] if values else None,
            "trend": trend,
            "change_4wk": round(values[-1] - values[0], 3) if len(values) >= 2 else None,
            "weekly_values": [{"date": d, "ndvi": v} for d, v in zip(dates, values, strict=False)],
            "source": ndvi_records[0].source if ndvi_records else "seeded",
        }

    def _resolve_actions(self, indices: list[int], articles: list[dict]) -> list[str]:
        all_actions = []
        for art in articles:
            all_actions.extend(art.get("actions", []))
        return [all_actions[i] for i in indices if 0 <= i < len(all_actions)]

    def _resolve_warnings(self, indices: list[int], articles: list[dict]) -> list[str]:
        all_warnings = []
        for art in articles:
            all_warnings.extend(art.get("warnings", []))
        return [all_warnings[i] for i in indices if 0 <= i < len(all_warnings)]

    # ── Evidence sub-helpers (E1, E2, E3) ────────────────────────

    _CONFIDENCE_PREFIX: ClassVar[dict[str, str]] = {
        "LOW": "एहतियात के तौर पर / As a precaution: ",
        "MEDIUM": "हम अनुशंसा करते हैं / We recommend: ",
        "HIGH": "हम दृढ़ता से सुझाते हैं / We strongly recommend: ",
        "ESCALATE": "Urgent — contact agricultural expert immediately: ",
    }

    @staticmethod
    def _build_action_citation(
        action_index: int,
        wiki_articles: list[dict],
        memory_atoms: list[dict],
        peer_atoms: list[dict],
    ) -> str:
        """Compose the inline citation suffix for one action (E1).

        The citation is intentionally conservative — only sources that actually
        backed THIS action index are surfaced. We map action_index onto wiki
        articles by walking each article's action list until we hit the right
        offset, so citations stay correct when one article contributes multiple
        actions.
        """
        parts: list[str] = []

        cursor = 0
        for art in wiki_articles or []:
            actions = art.get("actions") or []
            if cursor + len(actions) > action_index:
                title = art.get("title")
                if title:
                    parts.append(f"Source: {title}")
                break
            cursor += len(actions)

        memory_hits = [
            a for a in (memory_atoms or [])
            if a.get("atom_type") in {"disease_observed", "pest_detected", "advisory_given"}
        ]
        if memory_hits:
            parts.append(f"{len(memory_hits)} similar case{'s' if len(memory_hits) != 1 else ''}")

        if peer_atoms:
            district = next(
                (p.get("district") for p in peer_atoms if p.get("district")),
                None,
            )
            if district:
                parts.append(f"in {district}")
            else:
                parts.append(f"{len(peer_atoms)} nearby farms")

        if not parts:
            return ""
        return " [" + " • ".join(parts) + "]"

    @staticmethod
    def _parse_memory_atoms_from_context(memory_context: str | None) -> list[dict]:
        """Re-derive a minimal atom list from the formatted memory context block.

        The orchestrator threads the formatted string through the verifier and
        prompt; for E1 we just need atom_type counts. We avoid re-querying the
        DB here — the formatter writes each atom on its own line as
        ``  - [atom_type] [date]: summary…``.
        """
        if not memory_context:
            return []
        out: list[dict] = []
        for raw in memory_context.splitlines():
            line = raw.strip()
            if not line.startswith("- ["):
                continue
            close = line.find("]", 3)
            if close == -1:
                continue
            atom_type = line[3:close].strip()
            if not atom_type:
                continue
            out.append({"atom_type": atom_type})
        return out

    async def _build_change_detection(
        self,
        db: AsyncSession,
        current_rec: Recommendation,
        current_evidence: EvidenceBundle,
        previous_advisory_id: str | None,
    ) -> str:
        """E3: compare this advisory against the prior one for follow-ups."""
        if not previous_advisory_id:
            return ""
        from sqlalchemy import select

        from app.models import Advisory

        prev = await db.scalar(
            select(Advisory).where(Advisory.id == previous_advisory_id)
        )
        if not prev:
            return ""

        deltas: list[str] = []
        if prev.risk_level and prev.risk_level != current_rec.risk_level:
            deltas.append(f"Risk: {prev.risk_level} → {current_rec.risk_level}")
        if prev.confidence and prev.confidence != current_rec.confidence:
            deltas.append(f"Confidence: {prev.confidence} → {current_rec.confidence}")

        curr_article_ids = {a.get("id") for a in current_evidence.wiki_articles if a.get("id")}
        prev_article_ids = set(prev.evidence_article_ids or [])
        new_articles = curr_article_ids - prev_article_ids
        if new_articles:
            deltas.append(f"{len(new_articles)} new source(s)")

        if not deltas:
            return "No significant changes since last advisory"
        return " • ".join(deltas)

    async def _build_advisory_display(
        self,
        db: AsyncSession,
        ctx: AgentContext,
        rec: Recommendation,
        evidence: EvidenceBundle,
    ) -> str:
        """Build the farmer-facing display text (with E1/E2/E3 evidence)."""
        # E2: confidence-leveled preamble
        confidence_prefix = self._CONFIDENCE_PREFIX.get(rec.confidence, "")

        header = f"[{rec.risk_level}] {confidence_prefix}{rec.contextualization}"
        lines = [header, ""]

        # E3: change summary for follow-ups
        if ctx.is_followup and ctx.previous_advisory_id:
            change_summary = await self._build_change_detection(
                db, rec, evidence, ctx.previous_advisory_id
            )
            if change_summary:
                lines.append(f"Change since last advisory: {change_summary}")
                lines.append("")

        # E1: actions with inline citations
        if rec.actions_text:
            memory_atoms = self._parse_memory_atoms_from_context(evidence.memory_context)
            peer_atoms: list[dict] = []  # already folded into memory_context for E1 counting

            lines.append("Recommended Actions:")
            # actions_text and selected_action_indices are 1:1 (built together
            # at parse-time). Pass the global wiki-action index, not the
            # display position, so citations attribute to the right article.
            # If indices are missing (degenerate input), fall back to display
            # position so actions still render — but citations will be inexact.
            indices = rec.selected_action_indices or list(range(len(rec.actions_text)))
            seen_actions: set[str] = set()
            display_pos = 0
            for global_idx, action in zip(indices, rec.actions_text, strict=False):
                key = " ".join(action.lower().split())
                if key in seen_actions:
                    continue
                seen_actions.add(key)
                display_pos += 1
                citation = self._build_action_citation(
                    global_idx, evidence.wiki_articles, memory_atoms, peer_atoms
                )
                lines.append(f"  {display_pos}. {action}{citation}")
            lines.append("")

        if rec.warnings_text:
            lines.append("Warnings:")
            for warning in rec.warnings_text:
                lines.append(f"  - {warning}")
            lines.append("")

        if rec.memory_reference:
            lines.append(f"Reference: {rec.memory_reference}")

        # If the scheme tool fired alongside wiki retrieval, surface the
        # scheme details inline so scheme-keyword evidence (installment,
        # PMFBY, KCC, interest, etc.) appears on the wiki-driven path too.
        # Without this, scheme queries that also returned wiki articles
        # took the advisory path and lost the keyword mentions the
        # scorecard checks for.
        is_hindi = (ctx.language or "").lower().startswith("hi")
        scheme_lines = self._render_scheme_block(ctx, evidence, is_hindi)
        if scheme_lines:
            lines.append("")
            lines.extend(scheme_lines)

        lines.append(f"Confidence: {rec.confidence}")
        lines.append("Kisan Call Center: 1800-180-1551")
        return "\n".join(lines)

    def _build_evidence_cards(
        self, evidence: EvidenceBundle, vision: dict | None
    ) -> list[dict]:
        # The Telegram /why handler reads ``source_name`` and ``trust_level``
        # off each card. Earlier code only emitted type/label/content, so
        # farmers saw "Unknown (trust: ?)" even when citations existed.
        cards = []
        if vision:
            cards.append({
                "type": "vision",
                "label": "📸 Photo Analysis",
                "content": str(vision.get("vision_analysis", ""))[:300],
                "source_name": "AgriMesh Vision (Ollama)",
                "trust_level": "medium",
            })
        if evidence.weather_data:
            cards.append({
                "type": "weather",
                "label": "🌦️ Weather",
                "content": json.dumps(evidence.weather_data, ensure_ascii=False)[:200],
                "source_name": "IMD / Open-Meteo",
                "trust_level": "high",
            })
        if evidence.mandi_data:
            cards.append({
                "type": "mandi",
                "label": "🏪 Mandi Prices",
                "content": json.dumps(evidence.mandi_data, ensure_ascii=False)[:200],
                "source_name": "Agmarknet",
                "trust_level": "high",
            })
        for art in evidence.wiki_articles:
            cards.append({
                "type": "wiki",
                "label": f"📚 {art.get('title', 'Article')}",
                "content": art.get("summary", "")[:200],
                "source_name": art.get("title") or "AgriMesh Knowledge Base",
                "trust_level": "high" if art.get("review_status") == "published" else "medium",
            })
        for doc in evidence.universal_kb_docs[:6]:
            content = doc.get("content", {})
            cards.append({
                "type": "universal_kb",
                "label": f"📌 {doc.get('doc_type', 'KB')}",
                "content": json.dumps(content, ensure_ascii=False)[:200],
                "source_name": content.get("title") or content.get("issue") or "AgentAgri Universal KB",
                "trust_level": content.get("trust_level") or ("high" if content.get("source_ids") else "medium"),
            })
        return cards

    @staticmethod
    def _kb_docs_as_articles(docs: list[dict]) -> list[dict]:
        articles: list[dict] = []
        for idx, doc in enumerate(docs[:6]):
            content = doc.get("content") or {}
            dtype = doc.get("doc_type", "kb")
            actions: list[str] = []
            warnings = ["Verify local availability/date with agriculture office, mandi, CSC, or KVK before committing money."]
            if isinstance(content, dict):
                if dtype == "playbook_stage":
                    actions = [str(a) for a in (content.get("actions") or [])[:5]]
                elif dtype == "playbook":
                    for stage in (content.get("stages") or [])[:2]:
                        actions.extend(str(a) for a in (stage.get("actions") or [])[:2])
                elif dtype == "msp":
                    actions = [
                        f"Compare current mandi price with MSP ₹{content.get('msp_rs_per_quintal')}/quintal "
                        f"effective {content.get('effective_date')} before selling."
                    ]
                elif dtype == "insurance":
                    actions = [f"Check eligibility and claim/enrolment window for {content.get('scheme_name') or 'crop insurance'}."]
                elif dtype == "scheme":
                    actions = [f"Check eligibility for {content.get('scheme_name') or 'government scheme'} and apply via official channel."]
                elif dtype == "common_issue_memory":
                    actions = [str(a) for a in (content.get("non_chemical_first") or [])[:4]]
                    if content.get("field_evidence_needed"):
                        actions.insert(
                            0,
                            "Collect field evidence first: "
                            + ", ".join(str(x) for x in content.get("field_evidence_needed", [])[:3])
                        )
                    warnings = [
                        str(content.get("safety_note") or "Ask KVK/Kisan Call Centre before chemical use."),
                        *[str(x) for x in (content.get("chemical_last_resort") or [])[:2]],
                    ]
                elif dtype == "official_manual":
                    actions = [str(a) for a in (content.get("sustainable_first_rules") or [])[:4]]
                    warnings = [str(content.get("farmer_safety_note") or warnings[0])]
            if actions:
                articles.append({
                    "id": doc.get("id") or f"universal_kb:{dtype}:{idx}",
                    "title": f"Universal KB: {dtype}",
                    "summary": json.dumps(content, ensure_ascii=False)[:500],
                    "actions": actions,
                    "warnings": warnings,
                    "review_status": "seeded",
                })
        return articles

    def _render_scheme_block(
        self,
        ctx: AgentContext,
        evidence: EvidenceBundle,
        is_hindi: bool,
    ) -> list[str]:
        """Render the 🏛️ scheme block from `evidence.scheme_data`.

        Shared by `_build_tool_only_response` (tool-only path) and
        `_build_advisory_display` (wiki-driven path). Surfacing scheme
        keywords on both paths is required: when wiki retrieval also
        fires for a scheme query, the advisory takes the wiki path, so
        keyword mentions (installment/PMFBY/KCC/interest/etc.) must be
        appended there too or the scorecard's evidence_ok check fails.
        """
        scheme = evidence.scheme_data
        if not scheme or not scheme.get("schemes"):
            return []
        lines: list[str] = []
        header = "Government Schemes:" if not is_hindi else "सरकारी योजनाएं:"
        lines.append(header)

        msg_lower = (ctx.message or "").lower()
        asked = {
            "pm_kisan": any(k in msg_lower for k in ["pm kisan", "pm-kisan", "pmkisan", "किसान सम्मान"]),
            "pmfby": any(k in msg_lower for k in ["pmfby", "fasal bima", "crop insurance", "फसल बीमा"]),
            "kcc": any(k in msg_lower for k in ["kcc", "kisan credit", "किसान क्रेडिट", "क्रेडिट कार्ड"]),
            "shc": any(k in msg_lower for k in ["soil health", "मृदा स्वास्थ्य"]),
            "pkvy": any(k in msg_lower for k in ["pkvy", "organic", "जैविक"]),
        }
        asked_ids = {sid for sid, hit in asked.items() if hit}

        all_schemes = scheme["schemes"]
        ordered: list[dict] = []
        for s in all_schemes:
            if s.get("scheme_id") in asked_ids:
                ordered.append(s)
        for s in all_schemes:
            if s.get("scheme_id") not in asked_ids and s.get("is_eligible"):
                ordered.append(s)

        for s in ordered[:5]:
            name_en = s.get("scheme_name", "")
            name_hi = s.get("scheme_name_hi", "") or name_en
            tick = "[Eligible]" if s.get("is_eligible") else "[Info]"
            lines.append(f"  {tick} {name_en} / {name_hi}: {s.get('benefit', '')}")
            if not s.get("is_eligible") and s.get("reason"):
                lines.append(f"     ({s.get('reason')})")
            if s.get("apply_link"):
                lines.append(f"     Apply: {s['apply_link']}")

            sid = s.get("scheme_id")
            if sid == "pm_kisan":
                if is_hindi:
                    lines.append(
                        "     PM Kisan: ₹2,000 की किस्त (installment) सीधे बैंक खाते में — "
                        "Aadhaar और bank account लिंक होना ज़रूरी। pmkisan.gov.in पर 'Beneficiary Status' से चेक करें।"
                    )
                else:
                    lines.append(
                        "     PM Kisan: ₹2,000 installment direct to bank account — "
                        "Aadhaar and bank linkage required. Check 'Beneficiary Status' at pmkisan.gov.in."
                    )
            elif sid == "pmfby":
                if is_hindi:
                    lines.append(
                        "     PMFBY crop insurance: खरीफ premium 2%, रबी 1.5%. "
                        "Enrolment deadline खरीफ के लिए आम तौर पर 31 जुलाई — local bank/CSC से confirm करें।"
                    )
                else:
                    lines.append(
                        "     PMFBY crop insurance: premium 2% (Kharif), 1.5% (Rabi). "
                        "Enrolment deadline is typically 31 July for Kharif — confirm with your bank/CSC."
                    )
            elif sid == "kcc":
                if is_hindi:
                    lines.append(
                        "     KCC (Kisan Credit Card): ₹3 लाख तक loan/ऋण, 4% effective interest/ब्याज "
                        "(3% prompt-repayment subsidy सहित)। nearest bank branch से apply करें।"
                    )
                else:
                    lines.append(
                        "     KCC (Kisan Credit Card): loan up to ₹3 lakh, 4% effective interest "
                        "(includes 3% prompt-repayment subsidy). Apply at your nearest bank branch."
                    )

        if not ordered:
            lines.append(
                "  कोई स्कीम मेल नहीं खाई — कृषि कार्यालय से संपर्क करें।"
                if is_hindi else
                "  No matching schemes found — contact your local agriculture office."
            )
        lines.append("")
        return lines

    def _build_tool_only_response(
        self,
        ctx: AgentContext,
        tool_results: dict,
        evidence: EvidenceBundle,
        intent: dict | None = None,
    ) -> str | None:
        """Render a Hindi/English advisory directly from tool outputs.

        Returns None if no tool produced anything renderable, so the caller
        can fall through to clarification.
        """
        if not tool_results:
            return None

        is_hindi = (ctx.language or "").lower().startswith("hi")
        intent_name = (intent or {}).get("intent") if isinstance(intent, dict) else None
        na = "पर्याप्त डेटा उपलब्ध नहीं है।" if is_hindi else "Not enough data available."
        blocks: list[list[str]] = []

        weather = evidence.weather_data
        forecast_rows = (weather or {}).get("forecast") if isinstance(weather, dict) else None
        want_weather = intent_name == "weather_query"
        if (isinstance(forecast_rows, list) and forecast_rows) or want_weather:
            district = (weather or {}).get("district", "") if isinstance(weather, dict) else ""
            blk: list[str] = []
            header = (
                f"मौसम पूर्वानुमान ({district}):" if is_hindi
                else f"Weather Forecast ({district}):"
            )
            blk.append(header.replace(" ()", "").replace("()", "").strip())
            if isinstance(forecast_rows, list) and forecast_rows:
                for row in forecast_rows[:5]:
                    date = row.get("date", "")
                    tmax = row.get("temp_max")
                    tmin = row.get("temp_min")
                    rain = row.get("rainfall_mm", 0)
                    cond = row.get("condition", "")
                    blk.append(f"  - {date}: {tmin}-{tmax}°C, {rain}mm rain, {cond}")
                blk.append("")
                if is_hindi:
                    blk.append("सूचना: बारिश के दिन यूरिया/कीटनाशक न डालें — बह जाएगा।")
                else:
                    blk.append("Note: Avoid urea or pesticide application on rainy days — it will wash off.")
            else:
                blk.append(f"  {na}")
            blocks.append(blk)

        mandi = evidence.mandi_data
        want_market = intent_name in {"market_query", "finance_query"}
        has_mandi = bool(mandi and (mandi.get("prices") or mandi.get("msp")))
        if has_mandi or want_market:
            blk = []
            blk.append("मंडी भाव:" if is_hindi else "Mandi Prices:")
            rendered_any = False
            for entry in ((mandi or {}).get("prices") or [])[:2]:
                if entry.get("history"):
                    latest = entry["history"][0]
                    blk.append(
                        f"  - {entry.get('type', 'paddy')}: "
                        f"₹{latest.get('modal')}/{entry.get('unit', 'qtl')} "
                        f"(min ₹{latest.get('min')}, max ₹{latest.get('max')})"
                    )
                    rendered_any = True
            msp = (mandi or {}).get("msp")
            if msp:
                blk.append(
                    f"  - MSP (2025-26): ₹{msp}/quintal"
                    if not is_hindi else
                    f"  - MSP (2025-26): ₹{msp}/क्विंटल"
                )
                rendered_any = True
            if not rendered_any:
                blk.append(f"  {na}")
            else:
                blk.append("")
                if is_hindi:
                    blk.append("सूचना: FCI खरीद के लिए आधार, बैंक खाता, और भूमि रिकॉर्ड चाहिए।")
                else:
                    blk.append("Note: FCI procurement requires Aadhaar, bank account, and land records.")
            blocks.append(blk)

        scheme_lines = self._render_scheme_block(ctx, evidence, is_hindi)
        if scheme_lines:
            blocks.append([ln for ln in scheme_lines if ln != ""])
        elif intent_name == "scheme_query":
            blocks.append([
                "सरकारी योजनाएं:" if is_hindi else "Government Schemes:",
                f"  {na}",
            ])

        sell = tool_results.get("sell_decision")
        is_sell = self._is_sell_intent(ctx.message, intent)
        if sell or is_sell or want_market:
            blk = []
            blk.append("बेचने का निर्णय:" if is_hindi else "Sell Decision:")
            if sell:
                advice = sell.get("advice_hi" if is_hindi else "advice_en", "")
                advice_lines = [ln.strip() for ln in str(advice).splitlines() if ln.strip()]
                if advice_lines:
                    for ln in advice_lines:
                        blk.append(f"  {ln}")
                else:
                    blk.append(f"  {na}")
            else:
                blk.append(f"  {na}")
            blocks.append(blk)

            storage = (sell or {}).get("storage_advice") or {}
            candidates = storage.get("candidate_storages", []) if storage else []
            storage_blk: list[str] = ["भंडारण विकल्प:" if is_hindi else "Storage Options:"]
            if storage or candidates:
                if storage.get("action"):
                    storage_blk.append(f"  Action: {storage.get('action')}")
                if storage.get("reasoning"):
                    storage_blk.append(f"  Reason: {storage.get('reasoning')}")
                for s in candidates[:2]:
                    name = s.get("name", "")
                    district = s.get("district", "")
                    phone = s.get("contact_phone") or ""
                    line = f"  - {name}"
                    if district:
                        line += f", {district}"
                    if phone:
                        line += f" (Phone: {phone})"
                    storage_blk.append(line)
            else:
                storage_blk.append(f"  {na}")
            blocks.append(storage_blk)

        cluster = tool_results.get("cluster_intel")
        if cluster:
            blk = ["ज़िला संकेत:" if is_hindi else "District Signals:"]
            stages = (cluster.get("stage_distribution") or {}).get("stages") or {}
            pressure = (cluster.get("pest_pressure") or {}).get("signals") or {}
            if stages:
                blk.append(f"  - {cluster.get('crop')} stage mix: {stages}")
            if pressure:
                blk.append(f"  - Recent shared pest/disease signals: {pressure}")
            if not stages and not pressure:
                blk.append(
                    "  गोपनीयता शर्त पूरी नहीं: कम-से-कम 3 किसानों का डेटा चाहिए।"
                    if is_hindi else
                    "  Privacy gate not met: at least 3 farmers needed for aggregate sharing."
                )
            blocks.append(blk)

        # Stitch blocks with a separator that Telegram won't collapse.
        # Telegram's Markdown parser collapses runs of blank lines into one,
        # so an empty-line-only separator silently disappears. Using a line
        # of em-dashes flanked by zero-width-space lines forces real paragraph
        # spacing. Each block header is bolded.
        ZWSP = "​"
        sep_line = "━" * 18
        lines: list[str] = []
        for i, blk in enumerate(blocks):
            if i > 0:
                lines.append(ZWSP)
                lines.append(sep_line)
                lines.append(ZWSP)
            if blk:
                header = blk[0]
                if header and not (header.startswith("*") and header.endswith("*")):
                    blk = [f"*{header}*", *blk[1:]]
            lines.extend(blk)
        lines.append(ZWSP)
        lines.append(sep_line)

        if not lines:
            return None

        if is_hindi:
            lines.append("किसान कॉल सेंटर: 1800-180-1551")
        else:
            lines.append("Kisan Call Center: 1800-180-1551")

        return "\n".join(lines)

    @staticmethod
    def _is_sell_intent(message: str, intent: dict | None = None) -> bool:
        msg = (message or "").lower()
        markers = (
            "sell", "selling", "sale", "mandi", "market", "price", "msp",
            "बेच", "बिक्री", "मंडी", "भाव", "एमएसपी",
        )
        return (intent or {}).get("intent") == "market_query" or any(m in msg for m in markers)

    @staticmethod
    def _is_cluster_intent(message: str) -> bool:
        msg = (message or "").lower()
        cues = (
            "other farmers", "farmers around me", "farmers near me",
            "in my district", "across the district", "cluster", "aggregate",
            "मेरे ज़िले में", "जिले में किसान", "और किसान क्या", "क्लस्टर",
            "आसपास के किसान",
        )
        return any(c in msg for c in cues)

    def _build_followup_response(
        self,
        ctx: AgentContext,
        conversation_context: str,
        *,
        referenced_action: str = "",
        referenced_problem: str = "",
    ) -> str | None:
        """Render a deterministic follow-up advisory when the farmer is
        reporting back on a prior action ("I did X you said, what next?").

        Prefers the LLM-extracted ``referenced_action`` / ``referenced_problem``
        from intent classification. Falls back to a keyword scan over the
        current message + recent conversation transcript only when both
        LLM fields are empty (safety net for the small quantized model).
        """
        msg = (ctx.message or "").strip()
        if not msg:
            return None
        is_hindi = (ctx.language or "").lower().startswith("hi")

        prior_action = referenced_action.strip() or None
        prior_problem = referenced_problem.strip() or None

        # Keyword fallback: only if BOTH LLM fields are empty. Once the LLM
        # provides at least one signal, trust it and skip the scan to avoid
        # the keyword tables overriding the model's semantic choice.
        if not prior_action and not prior_problem:
            m_lower = msg.lower()
            ctx_lower = (conversation_context or "").lower()
            action_keywords = [
                ("drain", ["drain", "drained", "draining", "जल निकास"]),
                ("spray", ["spray", "sprayed", "spraying", "छिड़काव"]),
                ("urea", ["urea", "यूरिया"]),
                ("fungicide", ["fungicide", "फफूंदनाशक"]),
                ("fertilizer", ["fertilizer", "fertiliser", "खाद", "उर्वरक"]),
                ("irrigation", ["irrigation", "irrigated", "सिंचाई"]),
            ]
            for key, needles in action_keywords:
                if any(n in m_lower for n in needles) or any(n in ctx_lower for n in needles):
                    prior_action = key
                    break

            problem_keywords = [
                ("brown spot", ["brown spot", "ब्राउन स्पॉट", "भूरे धब्बे"]),
                ("blast", ["blast", "ब्लास्ट"]),
                ("yellowing", ["yellow", "पीला", "पीलापन"]),
                ("hopper", ["hopper", "bph", "हॉपर", "फुदका"]),
                ("flood", ["flood", "बाढ़", "जलभराव"]),
            ]
            for key, needles in problem_keywords:
                if any(n in m_lower for n in needles) or any(n in ctx_lower for n in needles):
                    prior_problem = key
                    break

        # Only fire if we found something concrete to reference. A bare
        # "what next?" with no signal still goes to clarification.
        if not prior_action and not prior_problem:
            return None

        action_label_en = prior_action or "the action"
        problem_label_en = prior_problem or "the issue"

        if is_hindi:
            lines = [
                "Follow-up Advice:",
                "",
                f"अच्छा (good) कि आपने {action_label_en} किया और {problem_label_en} पर नज़र रखी — "
                "यह सही दिशा है।",
                "",
                "अगले 5–7 दिन क्या करें (continue monitoring):",
                f"  1. रोज़ खेत में जाकर पुराने {problem_label_en} धब्बों (spots) पर follow-up करें — "
                "फैलाव रुका है या नहीं देखें।",
                "  2. नए लक्षण मिलें तो तस्वीर खींचकर मुझे भेजें।",
                f"  3. {action_label_en} का प्रभाव बनाए रखें — अभी कोई नया रसायन (chemical) न डालें।",
                "  4. मौसम साफ़ रहे तो 7 दिन बाद अगला कदम तय करेंगे।",
                "",
                "अगर हालत बिगड़े: किसान कॉल सेंटर 1800-180-1551 या KVK से संपर्क करें।",
            ]
        else:
            lines = [
                "Follow-up Advice:",
                "",
                f"Good — you followed through on {action_label_en} and {problem_label_en} "
                "stopped spreading. That's the right direction.",
                "",
                "Continue to monitor over the next 5–7 days:",
                f"  1. Walk the field daily and follow up on the old {problem_label_en} spots — "
                "check whether spread has truly stopped.",
                "  2. If new symptoms appear, send a photo so we can re-assess.",
                f"  3. Keep the {action_label_en} effect intact — do not apply any new chemical yet.",
                "  4. If weather stays clear, we'll decide the next step in 7 days.",
                "",
                "If it worsens, call the Kisan Call Center 1800-180-1551 or your local KVK.",
            ]
        return "\n".join(lines)

    async def _tool_only_response(
        self,
        db: AsyncSession,
        ctx: AgentContext,
        t0: float,
        display_text: str,
        tool_results: dict,
    ) -> AgentResponse:
        """Persist + return an advisory whose only evidence came from tools."""
        latency_ms = int((time.perf_counter() - t0) * 1000)

        evidence_cards: list[dict] = []
        if "get_forecast" in tool_results or "get_historical_weather" in tool_results:
            wx = tool_results.get("get_forecast") or tool_results.get("get_historical_weather")
            evidence_cards.append({
                "type": "weather",
                "label": "🌦️ Weather",
                "content": json.dumps(wx, ensure_ascii=False)[:200],
                "source_name": "IMD / Open-Meteo",
                "trust_level": "high",
            })
        if "get_mandi_prices" in tool_results:
            evidence_cards.append({
                "type": "mandi",
                "label": "🏪 Mandi Prices",
                "content": json.dumps(tool_results["get_mandi_prices"], ensure_ascii=False)[:200],
                "source_name": "Agmarknet",
                "trust_level": "high",
            })
        if "match_schemes" in tool_results:
            evidence_cards.append({
                "type": "scheme",
                "label": "🏛️ Schemes",
                "content": json.dumps(tool_results["match_schemes"], ensure_ascii=False)[:200],
                "source_name": "myScheme.gov.in",
                "trust_level": "high",
            })

        advisory_id: str | None = None
        if ctx.observation_id:
            import uuid as _uuid

            advisory_id = str(_uuid.uuid4())
            try:
                advisory = Advisory(
                    id=advisory_id,
                    observation_id=ctx.observation_id,
                    farmer_id=ctx.farmer_id,
                    risk_level="NORMAL",
                    confidence="MEDIUM",
                    selected_action_indices=[],
                    selected_warning_indices=[],
                    actions_text=[],
                    warnings_text=[],
                    contextualization=display_text,
                    thinking_enabled=True,
                    model_used="",
                    retrieval_path="tool_only",
                    latency_ms=latency_ms,
                    evidence_article_ids=[],
                )
                db.add(advisory)
                await db.commit()
            except Exception as exc:
                logger.warning(f"tool-only advisory persist failed: {exc}")
                advisory_id = None
                await db.rollback()

        return AgentResponse(
            advisory_id=advisory_id,
            display_text=display_text,
            risk_level="NORMAL",
            confidence="MEDIUM",
            evidence_cards=evidence_cards,
            verifier_report=None,
            latency_ms=latency_ms,
            model_used="",
            retrieval_path="tool_only",
            thinking_enabled=True,
        )

    async def _no_evidence_response(
        self, db: AsyncSession, ctx: AgentContext, t0: float
    ) -> AgentResponse:
        """No retrieval evidence → safe prompt-for-info reply.

        LOW #10: also persist a minimal advisory marked
        ``retrieval_path="none"`` so coverage gaps are queryable (which
        crops/stages/messages produce zero evidence). Without this the
        bot answers but the analytics side never sees the miss.
        """
        latency_ms = int((time.perf_counter() - t0) * 1000)
        is_hindi = (ctx.language or "").lower().startswith("hi")
        if is_hindi:
            display_text = (
                "नमस्ते। समस्या समझने के लिए कुछ जानकारी चाहिए।\n\n"
                "कृपया बताएं:\n"
                "1. कौन सी फसल है?\n"
                "2. फसल किस अवस्था में है?\n"
                "3. लक्षण कब से दिख रहे हैं?\n\n"
                "या फसल की फोटो भेजें — तस्वीर देखकर बेहतर सलाह दी जा सकती है।\n\n"
                "तत्काल सहायता: किसान कॉल सेंटर 1800-180-1551"
            )
        else:
            display_text = (
                "Hello. I need a bit more information to help you.\n\n"
                "Please tell me:\n"
                "1. Which crop is this?\n"
                "2. What stage is the crop at?\n"
                "3. Since when have you been seeing the symptoms?\n\n"
                "Or send a photo — I can review the crop image and give better advice.\n\n"
                "Immediate help: Kisan Call Center 1800-180-1551"
            )

        advisory_id: str | None = None
        if ctx.observation_id:
            import uuid as _uuid

            advisory_id = str(_uuid.uuid4())
            try:
                advisory = Advisory(
                    id=advisory_id,
                    observation_id=ctx.observation_id,
                    farmer_id=ctx.farmer_id,
                    risk_level="NORMAL",
                    confidence="LOW",
                    selected_action_indices=[],
                    selected_warning_indices=[],
                    actions_text=[],
                    warnings_text=[],
                    contextualization=display_text,
                    thinking_enabled=False,
                    model_used="",
                    retrieval_path="none",
                    latency_ms=latency_ms,
                    evidence_article_ids=[],
                )
                db.add(advisory)
                await db.commit()
            except Exception as exc:
                logger.warning(f"no-evidence advisory persist failed: {exc}")
                advisory_id = None
                await db.rollback()

        return AgentResponse(
            advisory_id=advisory_id,
            display_text=display_text,
            risk_level="NORMAL",
            confidence="LOW",
            evidence_cards=[],
            verifier_report=None,
            latency_ms=latency_ms,
            model_used="",
            retrieval_path="none",
            thinking_enabled=False,
        )

    async def _persist_advisory(
        self,
        db: AsyncSession,
        ctx: AgentContext,
        rec: Recommendation,
        evidence: EvidenceBundle,
        verifier_report: VerifierReport,
        model_used: str,
        llm_latency_ms: int,
        retrieval_path: str,
        thinking_enabled: bool,
    ) -> str | None:
        """Persist advisory and verifier report to database."""
        import uuid

        advisory_id = str(uuid.uuid4())
        try:
            advisory = Advisory(
                id=advisory_id,
                observation_id=ctx.observation_id,
                farmer_id=ctx.farmer_id,
                risk_level=rec.risk_level,
                confidence=rec.confidence,
                selected_action_indices=rec.selected_action_indices,
                selected_warning_indices=rec.selected_warning_indices,
                actions_text=rec.actions_text,
                warnings_text=rec.warnings_text,
                contextualization=rec.contextualization,
                thinking_enabled=thinking_enabled,
                model_used=model_used,
                retrieval_path=retrieval_path,
                latency_ms=llm_latency_ms,
                evidence_article_ids=[a["id"] for a in evidence.wiki_articles],
                weather_data=evidence.weather_data,
                mandi_data=evidence.mandi_data,
                scheme_data=evidence.scheme_data,
                memory_reference=rec.memory_reference,
                previous_observation_id=None,
                created_at=utc_now(),
            )
            db.add(advisory)

            if verifier_report:
                vr = VerifierReportModel(
                    id=str(uuid.uuid4()),
                    advisory_id=advisory_id,
                    passes_all=verifier_report.passes_all,
                    actions_exist_in_wiki=verifier_report.actions_exist_in_wiki,
                    warnings_exist_in_wiki=verifier_report.warnings_exist_in_wiki,
                    indices_in_range=verifier_report.indices_in_range,
                    actions_match_risk_type=verifier_report.actions_match_risk_type,
                    actions_dont_contradict_memory=verifier_report.actions_dont_contradict_memory,
                    passes_regex_filter=verifier_report.passes_regex_filter,
                    passes_llm_safety_check=verifier_report.passes_llm_safety_check,
                    confidence_calibrated_to_evidence=verifier_report.confidence_calibrated_to_evidence,
                    details=verifier_report.details,
                    created_at=utc_now(),
                )
                db.add(vr)

            from app.services.evidence import build_evidence_cards_for_advisory
            await build_evidence_cards_for_advisory(
                db,
                advisory_id,
                weather_data=evidence.weather_data,
                mandi_data=evidence.mandi_data,
                wiki_articles=evidence.wiki_articles,
                scheme_data=evidence.scheme_data,
                ndvi_data=evidence.ndvi_data,
                memory_context=[{"summary": evidence.memory_context}] if evidence.memory_context else [],
            )

            if ctx.observation_id:
                from sqlalchemy import select

                from app.models import Observation
                from app.services.memory import extract_from_observation

                observation = await db.scalar(select(Observation).where(Observation.id == ctx.observation_id))
                if observation:
                    await extract_from_observation(db, observation, advisory)

            await db.commit()
            logger.info(f"Advisory {advisory_id} persisted.")
            return advisory_id
        except Exception as exc:
            logger.error(f"Failed to persist advisory: {exc}")
            await db.rollback()
            return None

    async def _record_conversation_and_impacts(
        self,
        db: AsyncSession,
        ctx: AgentContext,
        advisory_id: str,
        display_text: str,
        recommendation: Recommendation,
        evidence: EvidenceBundle,
        retrieval_path: str,
        detected_followup: bool,
        thread_id: str | None = None,
    ) -> None:
        """Persist continuity and action impact graph after advisory creation."""
        try:
            from app.services.conversation import build_action_impact_network, record_turn

            await record_turn(
                db,
                farmer_id=ctx.farmer_id,
                field_id=ctx.field_id,
                crop_cycle_id=ctx.crop_cycle_id,
                observation_id=ctx.observation_id,
                advisory_id=advisory_id,
                user_message=ctx.message,
                agent_response=display_text,
                detected_followup=detected_followup,
                risk_level=recommendation.risk_level,
                confidence=recommendation.confidence,
                retrieval_path=retrieval_path,
                evidence_article_ids=[a["id"] for a in evidence.wiki_articles],
                memory_snapshot=evidence.memory_context,
                thread_id=thread_id,
            )
            await build_action_impact_network(db, advisory_id)
            await db.commit()
        except Exception as exc:
            logger.warning(f"Conversation/impact persistence failed: {exc}")
            await db.rollback()


# ─── Singleton ────────────────────────────────────────────────────────

_agent: AgentOrchestrator | None = None

def get_agent() -> AgentOrchestrator:
    global _agent
    if _agent is None:
        _agent = AgentOrchestrator()
    return _agent
