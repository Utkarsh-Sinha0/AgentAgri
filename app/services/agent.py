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
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Advisory,
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

        # ── Step 1: Intent classification ────────────────────────
        try:
            intent_result = await self.llm.classify_intent(ctx.message, ctx.language)
            intent = intent_result.get("parsed", {})
            needs_retrieval = intent.get("needs_retrieval", True)
            needs_tools = intent.get("needs_tool_call", False)
            logger.info(f"Intent: {intent.get('intent')}, retrieval={needs_retrieval}, tools={needs_tools}")
        except Exception as exc:
            logger.error(f"Intent classification failed: {exc}")
            needs_retrieval = True
            needs_tools = False

        # ── Step 1.5: Durable conversation routing ───────────────
        previous_article_ids: list[str] = []
        try:
            from app.services.conversation import looks_like_followup, previous_evidence_article_ids

            detected_followup = detected_followup or looks_like_followup(ctx.message)
            previous_article_ids = await previous_evidence_article_ids(
                db,
                farmer_id=ctx.farmer_id,
                field_id=ctx.field_id,
                crop_cycle_id=ctx.crop_cycle_id,
            )
        except Exception as exc:
            logger.warning(f"Conversation continuity lookup failed: {exc}")

        # ── Step 2: Speculative retrieval (fire in parallel) ─────
        retrieval_task = None
        if needs_retrieval:
            topic_tags = self._extract_topic_tags(ctx.message, vision_result)
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
        if "match_schemes" in tool_results:
            evidence.scheme_data = tool_results.get("match_schemes")

        # Load memory context
        evidence.memory_context = await self._load_memory_context(db, ctx)
        profile_context = await self._load_personal_profile_context(db, ctx)
        conversation_context = await self._load_conversation_context(db, ctx)
        evidence.memory_context = "\n".join(
            part for part in [profile_context, conversation_context, evidence.memory_context] if part
        )

        # Load NDVI satellite data (§10.3)
        evidence.ndvi_data = await self._load_ndvi_data(db, ctx)

        # ── Step 6: Template selection ───────────────────────────
        selection_result = None
        if evidence.wiki_articles:
            selection_result = await self.llm.select_template(
                ctx.message,
                evidence.wiki_articles,
                evidence.memory_context,
            )
        else:
            # No evidence — fall back to conservative response
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

    def _extract_topic_tags(self, message: str, vision_result: dict | None) -> list[str]:
        """Extract topic tags from the message and vision analysis."""
        tags = set()
        keywords = {
            "fungal_disease": ["फफूंद", "fungus", "blight", "rust", "smut", "mildew", "झुलसा", "धब्बा"],
            "pest": ["कीट", "insect", "pest", "caterpillar", "aphid", "borer", "सूंडी", "कीड़ा"],
            "nutrient_deficiency": ["पीला", "yellow", "nitrogen", "phosphorus", "potash", "zinc", "नाइट्रोजन", "यूरिया"],
            "water_management": ["पानी", "water", "drainage", "irrigation", "flood", "सिंचाई", "जलभराव"],
            "weather_damage": ["rain", "बारिश", "hail", "frost", "heat", "cold", "पाला"],
        }
        combined = message.lower()
        if vision_result:
            combined += " " + str(vision_result).lower()

        for tag, kws in keywords.items():
            if any(kw in combined for kw in kws):
                tags.add(tag)
        return list(tags) if tags else ["general"]

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

        crop_default = (ctx.crop_name if ctx else None) or "rice"
        field_default = ctx.field_id if ctx else None
        farmer_id = ctx.farmer_id if ctx else None

        if tool_name in ("get_forecast", "get_historical_weather"):
            params.setdefault("field_id", field_default)
            # planner may emit "days_ahead" etc.
            if "days_ahead" in params and "days" not in params:
                params["days"] = params.pop("days_ahead")
        elif tool_name == "get_mandi_prices":
            params.setdefault("crop", crop_default)
            # planner may emit "market"/"mandi" instead of "district"
            for alt in ("market", "mandi", "city"):
                if alt in params and "district" not in params:
                    params["district"] = params.pop(alt)
                else:
                    params.pop(alt, None)
        elif tool_name == "get_msp":
            params.setdefault("crop", crop_default)
        elif tool_name == "match_schemes":
            params.setdefault("crop", crop_default)
            if "farmer_profile" not in params and farmer_id:
                params["farmer_profile"] = {"farmer_id": farmer_id}
            if "field" not in params and field_default:
                params["field"] = {"field_id": field_default}

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
        "ESCALATE": "⚠️ तुरंत कृषि विशेषज्ञ से संपर्क करें / Urgent — contact expert: ",
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
                    parts.append(f"📚 {title}")
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
        risk_emoji = {"NORMAL": "🟢", "WATCH": "🟡", "PREVENTIVE_ACTION": "🟠", "ESCALATE": "🔴"}

        # E2: confidence-leveled preamble
        confidence_prefix = self._CONFIDENCE_PREFIX.get(rec.confidence, "")

        header = f"{risk_emoji.get(rec.risk_level, '🟡')} **{rec.risk_level}** — {confidence_prefix}{rec.contextualization}"
        lines = [header, ""]

        # E3: change summary for follow-ups
        if ctx.is_followup and ctx.previous_advisory_id:
            change_summary = await self._build_change_detection(
                db, rec, evidence, ctx.previous_advisory_id
            )
            if change_summary:
                lines.append(f"📊 पिछली सलाह से बदलाव / What changed: {change_summary}")
                lines.append("")

        # E1: actions with inline citations
        if rec.actions_text:
            memory_atoms = self._parse_memory_atoms_from_context(evidence.memory_context)
            peer_atoms: list[dict] = []  # already folded into memory_context for E1 counting

            lines.append("*अनुशंसित कार्य / Recommended Actions:*")
            # actions_text and selected_action_indices are 1:1 (built together
            # at parse-time). Pass the global wiki-action index, not the
            # display position, so citations attribute to the right article.
            # If indices are missing (degenerate input), fall back to display
            # position so actions still render — but citations will be inexact.
            indices = rec.selected_action_indices or list(range(len(rec.actions_text)))
            for display_pos, (global_idx, action) in enumerate(
                zip(indices, rec.actions_text), 1
            ):
                citation = self._build_action_citation(
                    global_idx, evidence.wiki_articles, memory_atoms, peer_atoms
                )
                lines.append(f"  {display_pos}. {action}{citation}")
            lines.append("")

        if rec.warnings_text:
            lines.append("*सावधानियां / Warnings:*")
            for warning in rec.warnings_text:
                lines.append(f"  ⚠️ {warning}")
            lines.append("")

        if rec.memory_reference:
            lines.append(f"📋 _{rec.memory_reference}_")

        lines.append(f"🎯 *Confidence:* {rec.confidence}")
        lines.append("📞 Kisan Call Center: 1800-180-1551")
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
        return cards

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
        display_text = (
            "🌾 नमस्ते! मुझे आपकी समस्या समझने के लिए और जानकारी चाहिए।\n\n"
            "कृपया बताएं:\n"
            "1. कौन सी फसल है?\n"
            "2. फसल किस अवस्था में है?\n"
            "3. लक्षण कब से दिख रहे हैं?\n\n"
            "या फोटो भेजें — मैं फसल की तस्वीर देखकर बेहतर सलाह दे सकता हूं। 📸\n\n"
            "📞 तत्काल सहायता: किसान कॉल सेंटर 1800-180-1551"
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
