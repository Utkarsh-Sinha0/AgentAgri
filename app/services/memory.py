"""
AgriMesh V4.0 — Living Memory Service
Three operators:
  1. Extract: convert farm events → structured MemoryAtom facts
  2. Coarsen: aggregate atoms → MemorySummary at village/tehsil/district/state/national
  3. Traverse: semantic retrieval across field history + regional summaries + wiki
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from math import exp

from loguru import logger
from sqlalchemy import desc, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Advisory,
    CropCalendarTask,
    CropCycle,
    Farmer,
    FinanceEntry,
    Observation,
    SatelliteNDVI,
)
from app.models_memory import (
    MemoryAtom,
    MemorySummary,
)
from app.utils.time import utc_now

# ─── M1: Temporal Decay Half-Lives (days) ─────────────────────────────
# Calibrated to India's monsoon/cropping cycle. Default 30d for unknown
# atom types; market/weather decay faster (volatile), outcomes decay
# slowly (we learn from them for a full season).
_HALF_LIVES_DAYS: dict[str, int] = {
    "disease_observed": 30,
    "pest_detected": 45,
    "market_timing": 7,
    "price_recorded": 7,
    "weather_event": 3,
    "outcome_reported": 90,
    "irrigation_event": 14,
    "soil_trend": 60,
    "fertilizer_applied": 21,
    "vision_analysis": 30,
    "advisory_given": 30,
    "observation_recorded": 30,
    "field_insight": 90,
    "voice_intent": 30,
    "playbook_stage_progress": 60,
    "cold_storage_planned": 45,
    "insurance_claim_filed": 90,
}


def _temporal_weight(event_at, atom_type: str | None) -> float:
    """Exponential decay weight in [0.0, 1.0].

    ``weight = exp(-ln(2) * days_ago / half_life)``. Atoms with no
    timestamp get a low 0.10 floor so they're still retrievable but
    deprioritised. Future-dated atoms (clock skew, manual edits) get
    weight 0.0 — we don't want them displacing real history.
    """
    if event_at is None:
        return 0.10

    half_life = _HALF_LIVES_DAYS.get(atom_type or "", 30)
    try:
        days_ago = (utc_now() - event_at).total_seconds() / 86400.0
    except Exception:
        return 0.10
    if days_ago < 0:
        return 0.0
    weight = exp(-0.693 * days_ago / float(half_life))
    return round(weight, 3)

# ═══════════════════════════════════════════════════════════════════════
# OPERATOR 1: EXTRACTION
# ═══════════════════════════════════════════════════════════════════════

async def extract_from_observation(
    db: AsyncSession,
    observation: Observation,
    advisory: Advisory | None = None,
) -> list[MemoryAtom]:
    """Extract memory atoms from a farmer observation + advisory."""
    atoms = []

    # Get farmer context
    farmer_result = await db.execute(select(Farmer).where(Farmer.id == observation.farmer_id))
    farmer = farmer_result.scalar_one_or_none()
    if not farmer:
        return atoms

    # Get crop-cycle context
    cycle_result = await db.execute(select(CropCycle).where(CropCycle.id == observation.crop_cycle_id))
    cycle = cycle_result.scalar_one_or_none()

    # Atom 1: Observation event itself
    obs_type = observation.observation_type or "text"
    obs_summary = f"{'📸 Photo' if obs_type == 'photo' else '📝 Text'} observation"
    if cycle:
        obs_summary += f" for {cycle.crop_name} at {cycle.current_stage} stage"
    if observation.text_content:
        obs_summary += f": {observation.text_content[:120]}"

    details = {
        "observation_id": observation.id,
        "observation_type": obs_type,
        "text_content": observation.text_content,
        "crop_name": cycle.crop_name if cycle else None,
        "crop_stage": cycle.current_stage if cycle else None,
        "has_photo": bool(observation.image_path),
    }

    obs_atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=observation.field_id,
        crop_cycle_id=observation.crop_cycle_id,
        atom_type="observation_recorded",
        summary=obs_summary,
        details=details,
        confidence=0.95,
        source_type="observation",
        source_id=observation.id,
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        state="Bihar",
        event_at=observation.created_at or utc_now(),
        is_private=True,
    )
    atoms.append(obs_atom)

    # Atom 3: Vision result (if photo was analyzed) — caused by the
    # observation. We create vision before advisory so the advisory can
    # link to vision (richer evidence) when present, else to the
    # observation directly.
    vision_atom = None
    if observation.vision_analysis:
        vision_atom = MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            field_id=observation.field_id,
            crop_cycle_id=observation.crop_cycle_id,
            atom_type="vision_analysis",
            summary=f"Vision AI analyzed crop photo: {str(observation.vision_analysis)[:150]}",
            details={
                "observation_id": observation.id,
                "vision_confidence": observation.vision_confidence,
                "vision_result": str(observation.vision_analysis)[:500],
            },
            confidence=observation.vision_confidence or 0.70,
            source_type="observation",
            source_id=observation.id,
            village=farmer.village,
            tehsil=farmer.tehsil,
            district=farmer.district,
            event_at=observation.created_at or utc_now(),
            is_private=True,
            causal_predecessor_atom_id=obs_atom.id,
        )
        atoms.append(vision_atom)

    # Atom 2: Advisory given (if exists) — caused by vision when
    # available, else by the raw observation.
    if advisory:
        adv_summary = f"Advisory: {advisory.risk_level} risk for {cycle.crop_name if cycle else 'crop'}"
        if advisory.contextualization:
            adv_summary += f" — {advisory.contextualization[:100]}"

        adv_atom = MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            field_id=observation.field_id,
            crop_cycle_id=observation.crop_cycle_id,
            atom_type="advisory_given",
            summary=adv_summary,
            details={
                "advisory_id": advisory.id,
                "risk_level": advisory.risk_level,
                "confidence": advisory.confidence,
                "actions": advisory.actions_text,
                "warnings": advisory.warnings_text,
                "model_used": advisory.model_used,
                "retrieval_path": advisory.retrieval_path,
            },
            confidence=0.80,
            source_type="advisory",
            source_id=advisory.id,
            village=farmer.village,
            tehsil=farmer.tehsil,
            district=farmer.district,
            event_at=advisory.created_at or utc_now(),
            is_private=True,
            # M4-safe: advisory_given is in the cross-farmer whitelist and the
            # summary carries no PII (only crop, risk, action labels).
            is_shareable=True,
            causal_predecessor_atom_id=(vision_atom.id if vision_atom else obs_atom.id),
        )
        atoms.append(adv_atom)

    # Persist all atoms
    for atom in atoms:
        db.add(atom)

    return atoms


async def extract_from_finance(
    db: AsyncSession,
    entry: FinanceEntry,
) -> MemoryAtom | None:
    """Extract memory atom from a finance entry."""
    farmer_result = await db.execute(select(Farmer).where(Farmer.id == entry.farmer_id))
    farmer = farmer_result.scalar_one_or_none()
    if not farmer:
        return None

    atom_type = "expense_logged" if entry.entry_type == "expense" else "revenue_logged"
    summary = f"{'💰 Expense' if entry.entry_type == 'expense' else '💵 Revenue'}: ₹{entry.amount:,.0f} on {entry.category}"
    if entry.description:
        summary += f" ({entry.description[:80]})"

    atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=None,
        crop_cycle_id=entry.crop_cycle_id,
        atom_type=atom_type,
        summary=summary,
        details={
            "finance_entry_id": entry.id,
            "amount": entry.amount,
            "category": entry.category,
            "entry_type": entry.entry_type,
        },
        confidence=0.99,
        source_type="finance",
        source_id=entry.id,
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        event_at=entry.recorded_at or utc_now(),
        is_private=True,
    )
    db.add(atom)
    return atom


async def extract_from_ndvi(
    db: AsyncSession,
    ndvi_record: SatelliteNDVI,
) -> MemoryAtom | None:
    """Extract memory atom from NDVI data."""
    atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id="system",
        field_id=ndvi_record.field_id,
        atom_type="ndvi_change",
        summary=f"Satellite NDVI: {ndvi_record.ndvi_value:.3f} on {ndvi_record.date.strftime('%Y-%m-%d') if ndvi_record.date else '?'}",
        details={"ndvi_value": ndvi_record.ndvi_value, "source": ndvi_record.source},
        confidence=0.85,
        source_type="ndvi",
        source_id=ndvi_record.id,
        event_at=ndvi_record.date or utc_now(),
        is_private=True,
    )
    db.add(atom)
    return atom


async def extract_from_task_completion(
    db: AsyncSession,
    task: CropCalendarTask,
    farmer_id: str,
    field_id: str,
) -> MemoryAtom | None:
    """Extract memory atom from a completed calendar task."""
    farmer_result = await db.execute(select(Farmer).where(Farmer.id == farmer_id))
    farmer = farmer_result.scalar_one_or_none()
    if not farmer or not task.completed:
        return None

    atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer_id,
        field_id=field_id,
        crop_cycle_id=task.cycle_id,
        atom_type="task_completed",
        summary=f"✅ Task completed: {task.task_name} at {task.stage} stage",
        details={"task_id": task.id, "task_name": task.task_name, "stage": task.stage},
        confidence=0.99,
        source_type="manual",
        source_id=task.id,
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        event_at=task.completed_at or utc_now(),
        is_private=True,
    )
    db.add(atom)
    return atom


# ═══════════════════════════════════════════════════════════════════════
# OPERATOR 2: COARSENING
# ═══════════════════════════════════════════════════════════════════════

async def coarsen_field_memory(
    db: AsyncSession,
    field_id: str,
    farmer_id: str,
) -> MemorySummary | None:
    """Aggregate all atoms for a field into a field-level summary."""
    atoms_result = await db.execute(
        select(MemoryAtom)
        .where(
            MemoryAtom.field_id == field_id,
            MemoryAtom.farmer_id == farmer_id,
        )
        .order_by(desc(MemoryAtom.event_at))
        .limit(200)
    )
    atoms = atoms_result.scalars().all()

    if not atoms:
        return None

    # Count by type
    type_counts = {}
    for a in atoms:
        type_counts[a.atom_type] = type_counts.get(a.atom_type, 0) + 1

    # Extract patterns
    patterns = []
    disease_atoms = [a for a in atoms if "disease" in (a.atom_type or "").lower()]
    if len(disease_atoms) >= 2:
        patterns.append(f"Recurring disease observations ({len(disease_atoms)} events)")

    expense_atoms = [a for a in atoms if a.atom_type == "expense_logged"]
    if expense_atoms:
        total_expense = sum((a.details or {}).get("amount", 0) for a in expense_atoms)
        patterns.append(f"Total expenses logged: ₹{total_expense:,.0f}")

    # Build summary
    summary_text = f"Field memory: {len(atoms)} events recorded. "
    summary_text += f"Types: {', '.join(f'{k}({v})' for k, v in sorted(type_counts.items()))}. "
    if patterns:
        summary_text += "Key patterns: " + "; ".join(patterns) + "."

    # Upsert existing summary
    existing = await db.execute(
        select(MemorySummary).where(
            MemorySummary.scale == "field",
            MemorySummary.scale_id == field_id,
        )
    )
    summary = existing.scalar_one_or_none()

    if summary:
        summary.summary_text = summary_text
        summary.key_patterns = patterns
        summary.stats = {"atom_types": type_counts}
        summary.atom_count = len(atoms)
        summary.confidence = min(0.95, 0.40 + (len(atoms) * 0.02))
        summary.last_updated = utc_now()
    else:
        summary = MemorySummary(
            id=str(uuid.uuid4()),
            scale="field",
            scale_id=field_id,
            title="Field Memory",
            summary_text=summary_text,
            key_patterns=patterns,
            stats={"atom_types": type_counts},
            atom_count=len(atoms),
            farmer_count=1,
            field_count=1,
            confidence=min(0.95, 0.40 + (len(atoms) * 0.02)),
            is_public=False,
        )
        db.add(summary)

    return summary


async def coarsen_village_memory(
    db: AsyncSession,
    village: str,
    district: str,
) -> MemorySummary | None:
    """
    Aggregate atoms across a village into village-level summary.
    Privacy: requires min 3 farmers. Coordinates NEVER shared.
    """
    atoms_result = await db.execute(
        select(MemoryAtom)
        .where(
            MemoryAtom.village == village,
            MemoryAtom.district == district,
        )
        .limit(500)
    )
    atoms = atoms_result.scalars().all()

    # Count unique farmers
    farmer_ids = set(a.farmer_id for a in atoms if a.farmer_id != "system")
    if len(farmer_ids) < 3:
        return None  # Privacy threshold not met

    field_ids = set(a.field_id for a in atoms if a.field_id)

    # Count disease/pest events
    disease_count = sum(1 for a in atoms if "disease" in (a.atom_type or "").lower())
    pest_count = sum(1 for a in atoms if "pest" in (a.atom_type or "").lower())

    patterns = []
    if disease_count >= 3:
        patterns.append(f"Disease events across {len(farmer_ids)} farmers ({disease_count} reports)")
    if pest_count >= 3:
        patterns.append(f"Pest events across {len(farmer_ids)} farmers ({pest_count} reports)")

    summary_text = (
        f"Village {village}: {len(atoms)} events from {len(farmer_ids)} farmers "
        f"across {len(field_ids)} fields."
    )

    existing = await db.execute(
        select(MemorySummary).where(
            MemorySummary.scale == "village",
            MemorySummary.scale_id == f"{district}:{village}",
        )
    )
    summary = existing.scalar_one_or_none()

    if summary:
        summary.summary_text = summary_text
        summary.key_patterns = patterns
        summary.atom_count = len(atoms)
        summary.farmer_count = len(farmer_ids)
        summary.field_count = len(field_ids)
        summary.last_updated = utc_now()
        summary.confidence = min(0.90, 0.30 + (len(atoms) * 0.01))
    else:
        summary = MemorySummary(
            id=str(uuid.uuid4()),
            scale="village",
            scale_id=f"{district}:{village}",
            title=f"Village Commons — {village}",
            summary_text=summary_text,
            key_patterns=patterns,
            atom_count=len(atoms),
            farmer_count=len(farmer_ids),
            field_count=len(field_ids),
            confidence=min(0.90, 0.30 + (len(atoms) * 0.01)),
            is_public=True,
            min_farmers_required=3,
        )
        db.add(summary)

    return summary


async def run_coarsening_job(db: AsyncSession) -> dict:
    """Nightly coarsening: run field → village → tehsil → district aggregations."""
    results = {"field": 0, "village": 0, "tehsil": 0, "district": 0}

    # Field-level: for each field with recent atoms
    fields_result = await db.execute(
        select(MemoryAtom.field_id, MemoryAtom.farmer_id).distinct()
    )
    for field_id, farmer_id in fields_result.all():
        if field_id:
            summary = await coarsen_field_memory(db, field_id, farmer_id)
            if summary:
                results["field"] += 1

    # Village-level: group by village+district
    village_result = await db.execute(
        select(MemoryAtom.village, MemoryAtom.district).distinct()
    )
    for village, district in village_result.all():
        if village and district:
            summary = await coarsen_village_memory(db, village, district)
            if summary:
                results["village"] += 1

    await db.commit()
    logger.info(f"Coarsening complete: {results}")
    return results


# ═══════════════════════════════════════════════════════════════════════
# OPERATOR 3: TRAVERSAL
# ═══════════════════════════════════════════════════════════════════════

async def retrieve_memory_context(
    db: AsyncSession,
    farmer_id: str,
    field_id: str | None = None,
    crop_cycle_id: str | None = None,
    crop_name: str | None = None,
    crop_stage: str | None = None,
    risk_type: str | None = None,
    top_k: int = 8,
) -> list[dict]:
    """
    Semantic memory retrieval replacing 'latest 5 observations'.
    Filters by farmer, field, crop, stage, risk type.
    Returns top-k most relevant atoms + village-level summaries.
    """
    results = []

    # 1. Field-level atoms — fetch a wider pool (top_k * 6, capped at 50)
    # then rank by temporal-decay-weighted confidence (M1). This replaces
    # the previous "last N by date" semantics so old-but-confident atoms
    # for slow-cycling risks (pests, outcomes) aren't crowded out by
    # noisier recent activity.
    field_query = select(MemoryAtom).where(MemoryAtom.farmer_id == farmer_id)
    if field_id:
        field_query = field_query.where(MemoryAtom.field_id == field_id)
    if crop_cycle_id:
        field_query = field_query.where(MemoryAtom.crop_cycle_id == crop_cycle_id)
    if risk_type:
        field_query = field_query.where(
            or_(
                MemoryAtom.atom_type.ilike(f"%{risk_type}%"),
                MemoryAtom.summary.ilike(f"%{risk_type}%"),
            )
        )

    pool_size = min(50, max(top_k * 6, top_k))
    field_query = field_query.order_by(desc(MemoryAtom.event_at)).limit(pool_size)
    field_result = await db.execute(field_query)
    pool = list(field_result.scalars().all())

    scored: list[tuple[MemoryAtom, float, float]] = []
    for atom in pool:
        tw = _temporal_weight(atom.event_at, atom.atom_type)
        relevance = float(atom.confidence or 0.0) * tw
        scored.append((atom, tw, relevance))
    scored.sort(key=lambda r: r[2], reverse=True)

    for atom, tw, relevance in scored[:top_k]:
        results.append({
            "type": "field_memory",
            "atom_type": atom.atom_type,
            "summary": atom.summary,
            "confidence": atom.confidence,
            "event_at": atom.event_at.isoformat() if atom.event_at else None,
            "temporal_weight": tw,
            "relevance_score": round(relevance, 3),
            "source": "memory",
        })

    # 2. Village-level public summaries
    if field_id:
        # Get farmer's village
        farmer_result = await db.execute(select(Farmer).where(Farmer.id == farmer_id))
        farmer = farmer_result.scalar_one_or_none()
        if farmer and farmer.village:
            village_summary = await db.execute(
                select(MemorySummary)
                .where(
                    MemorySummary.scale == "village",
                    MemorySummary.scale_id == f"{farmer.district}:{farmer.village}",
                )
                .order_by(desc(MemorySummary.last_updated))
                .limit(1)
            )
            vs = village_summary.scalar_one_or_none()
            if vs:
                results.append({
                    "type": "village_summary",
                    "title": vs.title,
                    "summary": vs.summary_text,
                    "patterns": vs.key_patterns,
                    "farmer_count": vs.farmer_count,
                    "source": "memory",
                })

    return results


async def seed_memory_from_existing(db: AsyncSession) -> dict:
    """One-time backfill: extract atoms from all existing observations, advisories, and finance entries."""
    stats = {"observations": 0, "advisories": 0, "finance": 0, "total_atoms": 0}

    # Extract from all observations with advisories
    obs_result = await db.execute(
        select(Observation).limit(200)
    )
    for obs in obs_result.scalars().all():
        adv_result = await db.execute(
            select(Advisory).where(Advisory.observation_id == obs.id).limit(1)
        )
        advisory = adv_result.scalar_one_or_none()
        atoms = await extract_from_observation(db, obs, advisory)
        stats["observations"] += 1
        stats["total_atoms"] += len(atoms)

    # Extract from all finance entries
    fin_result = await db.execute(select(FinanceEntry).limit(100))
    for entry in fin_result.scalars().all():
        atom = await extract_from_finance(db, entry)
        if atom:
            stats["finance"] += 1
            stats["total_atoms"] += 1

    await db.commit()
    logger.info(f"Memory backfill complete: {stats}")
    return stats


# ═══════════════════════════════════════════════════════════════════════
# M3: CAUSAL CHAIN TRAVERSAL
# ═══════════════════════════════════════════════════════════════════════

async def get_causal_chain(
    db: AsyncSession,
    atom_id: str,
    max_hops: int = 6,
) -> list[MemoryAtom]:
    """Walk causal_predecessor_atom_id from `atom_id` backwards.

    Returns the chain newest-first (the requested atom is index 0,
    its predecessor at index 1, …). Stops at the first NULL predecessor,
    after ``max_hops`` traversals, or if a cycle is detected.
    """
    chain: list[MemoryAtom] = []
    seen: set[str] = set()
    current_id: str | None = atom_id
    while current_id and len(chain) < max_hops:
        if current_id in seen:
            logger.error(f"Circular causal chain detected at {current_id}")
            break
        seen.add(current_id)
        atom = await db.scalar(select(MemoryAtom).where(MemoryAtom.id == current_id))
        if atom is None:
            break
        chain.append(atom)
        current_id = atom.causal_predecessor_atom_id
    return chain


# ═══════════════════════════════════════════════════════════════════════
# M4: CROSS-FARMER LEARNING (PRIVACY-SAFE)
# ═══════════════════════════════════════════════════════════════════════

async def retrieve_similar_farm_context(
    db: AsyncSession,
    farmer_id: str,
    crop_name: str | None,
    district: str | None,
    top_k: int = 5,
    min_farmers_for_privacy: int = 3,
    lookback_days: int = 60,
) -> list[dict]:
    """Return anonymised atoms from other farmers in the same district.

    Privacy contract:
      * Atoms from the requesting farmer are excluded.
      * Only released when at least ``min_farmers_for_privacy`` distinct
        peer farmers contribute (default 3 — matches MemorySummary's
        village-commons gate).
      * Output omits farmer_id / field_id / village / GPS — only the
        coarse district, atom_type, summary, confidence, and event_at
        leave the function.
    """
    if not district:
        return []

    cutoff = utc_now() - timedelta(days=lookback_days)
    query = (
        select(MemoryAtom)
        .where(
            MemoryAtom.district == district,
            MemoryAtom.farmer_id != farmer_id,
            MemoryAtom.farmer_id != "system",
            MemoryAtom.event_at >= cutoff,
            MemoryAtom.atom_type.in_(
                ["disease_observed", "pest_detected", "advisory_given", "outcome_reported"]
            ),
            # Belt-and-braces: only atoms explicitly flagged shareable at
            # extract time leak across farmers, even within the whitelist.
            MemoryAtom.is_shareable.is_(True),
        )
        .order_by(desc(MemoryAtom.event_at))
        .limit(200)
    )
    # Topic relevance: prefer atoms whose summary mentions the crop.
    if crop_name:
        query = query.where(MemoryAtom.summary.ilike(f"%{crop_name}%"))

    pool = (await db.execute(query)).scalars().all()
    if not pool:
        return []

    # Keep one most-recent atom per peer farmer, then enforce the gate.
    per_farmer: dict[str, MemoryAtom] = {}
    for atom in pool:
        if atom.farmer_id not in per_farmer:
            per_farmer[atom.farmer_id] = atom

    if len(per_farmer) < min_farmers_for_privacy:
        logger.info(
            f"cross-farm learning: only {len(per_farmer)} peer farmers "
            f"(< {min_farmers_for_privacy}); withholding context"
        )
        return []

    out: list[dict] = []
    for atom in list(per_farmer.values())[:top_k]:
        out.append({
            "type": "similar_farm",
            "atom_type": atom.atom_type,
            "summary": atom.summary,
            "confidence": atom.confidence,
            "event_at": atom.event_at.isoformat() if atom.event_at else None,
            "district": atom.district,
            "source": "cross_farmer",
        })
    return out


# ═══════════════════════════════════════════════════════════════════════
# M2: OUTCOME-WEIGHTED CONFIDENCE BOOST
# ═══════════════════════════════════════════════════════════════════════

# Delta lookup keyed by (result, rating_bucket). Bucket = "high" if
# rating>=4, "mid" if >=3, "low" otherwise. Worsened outcomes always
# subtract; not_tried stays neutral.
_OUTCOME_DELTAS = {
    ("improved", "high"): 0.10,
    ("improved", "mid"): 0.05,
    ("improved", "low"): 0.02,
    ("worsened", "high"): -0.08,
    ("worsened", "mid"): -0.05,
    ("worsened", "low"): -0.03,
}


def _outcome_delta(result: str, rating: int) -> float:
    bucket = "high" if rating >= 4 else ("mid" if rating >= 3 else "low")
    return _OUTCOME_DELTAS.get((result.lower(), bucket), 0.0)


async def extract_from_outcome(
    db: AsyncSession,
    observation_id: str,
    result: str,
    comment: str,
    rating: int,
) -> MemoryAtom | None:
    """Record outcome and adjust confidence of every atom in its chain.

    M2 implementation:
      1. Create the ``outcome_reported`` atom and link it back to the
         related ``advisory_given`` atom (if found) via
         ``causal_predecessor_atom_id``.
      2. Walk the chain from that advisory atom back to its root and
         adjust confidence by ``_outcome_delta(result, rating)``, clamped
         to [0.0, 1.0].
      3. If no advisory atom is found (e.g. observation predates atom
         extraction), fall back to recent disease/pest atoms for the
         same farmer + crop so feedback isn't dropped silently.
    """
    obs = await db.scalar(select(Observation).where(Observation.id == observation_id))
    if obs is None:
        logger.warning(f"extract_from_outcome: observation {observation_id} not found")
        return None

    farmer = await db.scalar(select(Farmer).where(Farmer.id == obs.farmer_id))

    rating = max(1, min(5, int(rating)))
    outcome_confidence = 0.95 if rating >= 4 else (0.85 if rating >= 3 else 0.75)

    # Locate the advisory_given atom for this observation, if any. We
    # look up via Advisory.observation_id rather than scanning atoms
    # directly so that advisories without atoms (legacy data) don't
    # break the lookup.
    adv = await db.scalar(
        select(Advisory)
        .where(Advisory.observation_id == observation_id)
        .order_by(desc(Advisory.created_at))
        .limit(1)
    )
    predecessor_id: str | None = None
    if adv is not None:
        adv_atom = await db.scalar(
            select(MemoryAtom)
            .where(
                MemoryAtom.source_type == "advisory",
                MemoryAtom.source_id == adv.id,
                MemoryAtom.atom_type == "advisory_given",
            )
            .limit(1)
        )
        if adv_atom is not None:
            predecessor_id = adv_atom.id

    outcome_atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=obs.farmer_id,
        field_id=obs.field_id,
        crop_cycle_id=obs.crop_cycle_id,
        atom_type="outcome_reported",
        # PII contract: the summary is the only field exposed by
        # ``retrieve_similar_farm_context`` once the k-anonymity gate opens,
        # so it must carry no farmer free-text. Keep result/rating only.
        # The raw comment stays in ``details`` (field-scope) for the
        # owning farmer's own context.
        summary=f"Outcome: {result} (rating {rating}/5)",
        details={
            "result": result,
            "comment": comment,
            "rating": rating,
            "observation_id": observation_id,
            "advisory_id": adv.id if adv else None,
        },
        confidence=outcome_confidence,
        source_type="farmer_reported",
        source_id=observation_id,
        village=getattr(farmer, "village", None) if farmer else None,
        tehsil=getattr(farmer, "tehsil", None) if farmer else None,
        district=getattr(farmer, "district", None) if farmer else None,
        state=getattr(farmer, "state", None) if farmer else None,
        event_at=utc_now(),
        is_private=True,
        # M4-safe: outcome_reported is in the cross-farmer whitelist; summary
        # contains only result/rating, no PII or free-text beyond a truncated
        # comment fragment.
        is_shareable=True,
        causal_predecessor_atom_id=predecessor_id,
    )
    db.add(outcome_atom)

    # Walk the chain and apply the confidence delta.
    delta = _outcome_delta(result, rating)
    boosted_ids: list[str] = []
    if delta != 0.0:
        if predecessor_id:
            chain = await get_causal_chain(db, predecessor_id, max_hops=6)
        else:
            # Fallback: most recent disease/pest atoms for the same farmer
            # within the last 30 days. Scope by field/crop-cycle when the
            # outcome's observation carries them so a wheat outcome on one
            # field can't shift confidence on a parallel rice cycle. Only
            # widen the query if the scoped lookup finds nothing.
            cutoff = utc_now() - timedelta(days=30)
            base_filter = (
                MemoryAtom.farmer_id == obs.farmer_id,
                MemoryAtom.atom_type.in_(
                    ["disease_observed", "pest_detected", "vision_analysis"]
                ),
                MemoryAtom.event_at >= cutoff,
            )
            scoped_filter = list(base_filter)
            if obs.field_id:
                scoped_filter.append(MemoryAtom.field_id == obs.field_id)
            if obs.crop_cycle_id:
                scoped_filter.append(MemoryAtom.crop_cycle_id == obs.crop_cycle_id)

            fallback = (
                await db.execute(
                    select(MemoryAtom)
                    .where(*scoped_filter)
                    .order_by(desc(MemoryAtom.event_at))
                    .limit(3)
                )
            ).scalars().all()
            if not fallback and (obs.field_id or obs.crop_cycle_id):
                fallback = (
                    await db.execute(
                        select(MemoryAtom)
                        .where(*base_filter)
                        .order_by(desc(MemoryAtom.event_at))
                        .limit(3)
                    )
                ).scalars().all()
            chain = list(fallback)

        for atom in chain:
            before = float(atom.confidence or 0.0)
            after = max(0.0, min(1.0, before + delta))
            if after != before:
                atom.confidence = after
                boosted_ids.append(atom.id)

    try:
        await db.commit()
    except Exception as exc:
        await db.rollback()
        logger.error(f"extract_from_outcome: commit failed: {exc}")
        return None

    logger.info(
        "extract_from_outcome: recorded outcome atom "
        f"(obs={observation_id}, delta={delta:+.2f}, boosted={len(boosted_ids)})"
    )
    return outcome_atom
