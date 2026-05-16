"""
Outbreak warning service.

When a farmer reports a pest or disease problem (via /outcome with a negative
result, or via an observation tagged as pest/disease symptom), this module
checks whether enough farmers in the same village/tehsil/district have
reported a similar issue on the same crop within a recent window. If
reporting farmers are >= 10% of the registered farmers growing that crop in
that scope, an outbreak alert is created, matching farmers are pushed a
Telegram warning, an OutbreakAlert memory atom is written for each, and the
next agent reply to each prepends a one-line banner.

Scope cascade: village -> tehsil -> district. The smallest scope that
crosses the 10% threshold wins; we never re-fire at a coarser scope if a
finer one already alerted on the same (crop, pest_or_disease) within the
expiry window.

Single source of truth for the threshold and window lives at module level
so tests and the operator can tune them.
"""
from __future__ import annotations

from datetime import timedelta
from typing import Iterable
from uuid import uuid4

from loguru import logger
from sqlalchemy import and_, desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AlertCluster, AlertKind, AlertStatus, CropCycle, Farmer, Field, Observation
from app.models_memory import MemoryAtom
from app.utils.time import utc_now

# ─── Tunables ─────────────────────────────────────────────────────────

OUTBREAK_THRESHOLD = 0.10           # 10% of registered farmers in scope
OUTBREAK_REPORT_WINDOW_DAYS = 14    # how far back reports count
OUTBREAK_ALERT_TTL_DAYS = 14        # how long an alert is active
OUTBREAK_MIN_REPORTERS = 2          # absolute floor so a 1-farmer village never auto-trips
SCOPE_CASCADE = ("village", "tehsil", "district")

# atom types that count as "the farmer reported a problem"
SYMPTOM_ATOM_TYPES = ("disease_observed", "pest_detected")

# /outcome results that count as a problem report
NEGATIVE_OUTCOME_RESULTS = ("worsened", "no_change")


# ─── Helpers ──────────────────────────────────────────────────────────


def _normalize_label(label: str | None) -> str:
    if not label:
        return ""
    return label.strip().lower().replace("_", " ")


async def _farmer_for(db: AsyncSession, farmer_id: str) -> Farmer | None:
    return await db.scalar(select(Farmer).where(Farmer.id == farmer_id))


async def _active_crop_for(db: AsyncSession, farmer_id: str) -> str | None:
    """Most recent active crop name for the farmer (any field)."""
    row = await db.execute(
        select(CropCycle.crop_name)
        .join(Field, Field.id == CropCycle.field_id)
        .where(Field.farmer_id == farmer_id, CropCycle.is_active.is_(True))
        .order_by(desc(CropCycle.created_at))
        .limit(1)
    )
    return row.scalar_one_or_none()


async def _registered_farmers_in_scope(
    db: AsyncSession,
    *,
    crop_name: str,
    scope: str,
    village: str | None,
    tehsil: str | None,
    district: str | None,
) -> list[str]:
    """Distinct farmer IDs growing `crop_name` (active cycle) in the given scope."""
    stmt = (
        select(Farmer.id)
        .join(Field, Field.farmer_id == Farmer.id)
        .join(CropCycle, CropCycle.field_id == Field.id)
        .where(
            Farmer.is_active.is_(True),
            CropCycle.is_active.is_(True),
            func.lower(CropCycle.crop_name) == crop_name.lower(),
        )
        .distinct()
    )
    if scope == "village" and village:
        stmt = stmt.where(func.lower(Farmer.village) == village.lower())
    elif scope == "tehsil" and tehsil:
        stmt = stmt.where(func.lower(Farmer.tehsil) == tehsil.lower())
    elif scope == "district" and district:
        stmt = stmt.where(func.lower(Farmer.district) == district.lower())
    else:
        return []
    res = await db.execute(stmt)
    return [row[0] for row in res.all()]


async def _reporting_farmer_ids(
    db: AsyncSession,
    *,
    crop_name: str,
    pest_or_disease: str,
    farmer_ids_in_scope: Iterable[str],
    window_start,
) -> set[str]:
    """Farmers in scope who reported this pest/disease in the window."""
    farmer_ids = list(farmer_ids_in_scope)
    if not farmer_ids:
        return set()
    label = _normalize_label(pest_or_disease)

    reporting: set[str] = set()

    # 1. Symptom atoms (disease_observed / pest_detected) — match summary text.
    atom_rows = await db.execute(
        select(MemoryAtom.farmer_id, MemoryAtom.summary)
        .where(
            MemoryAtom.farmer_id.in_(farmer_ids),
            MemoryAtom.atom_type.in_(SYMPTOM_ATOM_TYPES),
            MemoryAtom.event_at >= window_start,
        )
    )
    for fid, summary in atom_rows.all():
        if label and label in (summary or "").lower():
            reporting.add(fid)

    # 2. Negative /outcome reports on the same crop — match outcome_text.
    obs_rows = await db.execute(
        select(Observation.farmer_id, Observation.outcome_text, CropCycle.crop_name)
        .join(CropCycle, CropCycle.id == Observation.crop_cycle_id)
        .where(
            Observation.farmer_id.in_(farmer_ids),
            Observation.outcome_logged_at >= window_start,
            func.lower(CropCycle.crop_name) == crop_name.lower(),
        )
    )
    for fid, outcome_text, _ in obs_rows.all():
        if not outcome_text:
            continue
        otext = outcome_text.lower()
        if any(neg in otext for neg in NEGATIVE_OUTCOME_RESULTS) and (
            not label or label in otext
        ):
            reporting.add(fid)

    return reporting


async def _existing_active_alert(
    db: AsyncSession,
    *,
    crop_name: str,
    pest_or_disease: str,
    village: str | None,
    tehsil: str | None,
    district: str | None,
) -> AlertCluster | None:
    """Return any non-expired outbreak alert covering this (crop, threat, area)."""
    label = _normalize_label(pest_or_disease)
    now = utc_now()
    stmt = (
        select(AlertCluster)
        .where(
            AlertCluster.kind == AlertKind.OUTBREAK,
            func.lower(AlertCluster.crop_name) == crop_name.lower(),
            func.lower(AlertCluster.pest_or_disease) == label,
            AlertCluster.expires_at > now,
        )
        .order_by(desc(AlertCluster.created_at))
    )
    res = await db.execute(stmt)
    for alert in res.scalars().all():
        # Hit if the alert's geographic scope contains the new report's area.
        if alert.scope == "village" and alert.village and village and alert.village.lower() == village.lower():
            return alert
        if alert.scope == "tehsil" and alert.tehsil and tehsil and alert.tehsil.lower() == tehsil.lower():
            return alert
        if alert.scope == "district" and alert.district and district and alert.district.lower() == district.lower():
            return alert
    return None


# ─── Public API ───────────────────────────────────────────────────────


async def check_and_trigger_outbreak(
    db: AsyncSession,
    *,
    reporter_farmer_id: str,
    crop_name: str,
    pest_or_disease: str,
) -> AlertCluster | None:
    """
    Run the cascade for one farmer's report. Returns the AlertCluster if a
    new outbreak was created OR an existing one was extended to cover this
    reporter; returns None if no threshold was crossed.

    Safe to call from request paths — all writes are committed before return.
    """
    label = _normalize_label(pest_or_disease)
    if not label or not crop_name:
        return None

    farmer = await _farmer_for(db, reporter_farmer_id)
    if farmer is None:
        return None

    window_start = utc_now() - timedelta(days=OUTBREAK_REPORT_WINDOW_DAYS)

    for scope in SCOPE_CASCADE:
        if scope == "village" and not farmer.village:
            continue
        if scope == "tehsil" and not farmer.tehsil:
            continue
        if scope == "district" and not farmer.district:
            continue

        farmer_ids_in_scope = await _registered_farmers_in_scope(
            db,
            crop_name=crop_name,
            scope=scope,
            village=farmer.village,
            tehsil=farmer.tehsil,
            district=farmer.district,
        )
        if len(farmer_ids_in_scope) < OUTBREAK_MIN_REPORTERS:
            continue  # too sparse to be meaningful

        reporters = await _reporting_farmer_ids(
            db,
            crop_name=crop_name,
            pest_or_disease=label,
            farmer_ids_in_scope=farmer_ids_in_scope,
            window_start=window_start,
        )
        # The current reporter always counts even if their atom/outcome
        # has not been flushed yet.
        reporters.add(reporter_farmer_id)

        if len(reporters) < OUTBREAK_MIN_REPORTERS:
            continue

        ratio = len(reporters) / len(farmer_ids_in_scope)
        if ratio < OUTBREAK_THRESHOLD:
            continue

        # Threshold crossed at this scope. Reuse an existing live alert if any.
        existing = await _existing_active_alert(
            db,
            crop_name=crop_name,
            pest_or_disease=label,
            village=farmer.village if scope == "village" else None,
            tehsil=farmer.tehsil if scope == "tehsil" else None,
            district=farmer.district if scope == "district" else None,
        )
        if existing is not None:
            updated = False
            current_reporters = set(existing.reporting_farmer_ids or [])
            new_reporters = reporters - current_reporters
            if new_reporters:
                existing.reporting_farmer_ids = list(current_reporters | new_reporters)
                existing.farmer_count = len(existing.reporting_farmer_ids)
                updated = True
            if updated:
                await db.commit()
            return existing

        alert = AlertCluster(
            id=str(uuid4()),
            kind=AlertKind.OUTBREAK,
            scope=scope,
            crop_name=crop_name,
            issue_category="pest_disease_outbreak",
            pest_or_disease=label,
            district=farmer.district or "",
            tehsil=farmer.tehsil or "" if scope in ("tehsil", "village") else (farmer.tehsil or ""),
            village=farmer.village if scope == "village" else None,
            observation_ids=[],
            advisory_ids=[],
            reporting_farmer_ids=list(reporters),
            notified_farmer_ids=[],
            consumed_farmer_ids=[],
            farmer_count=len(reporters),
            severity=min(0.50 + ratio, 0.99),
            status=AlertStatus.BROADCAST,
            expires_at=utc_now() + timedelta(days=OUTBREAK_ALERT_TTL_DAYS),
            broadcast_at=utc_now(),
        )
        db.add(alert)
        await db.commit()
        await db.refresh(alert)
        logger.info(
            "Outbreak alert created: scope={} crop={} threat={} reporters={}/{} ratio={:.0%}",
            scope, crop_name, label, len(reporters), len(farmer_ids_in_scope), ratio,
        )
        return alert

    return None


async def list_target_farmers(
    db: AsyncSession,
    alert: AlertCluster,
) -> list[Farmer]:
    """All farmers who should be warned by this alert, excluding reporters."""
    stmt = (
        select(Farmer)
        .join(Field, Field.farmer_id == Farmer.id)
        .join(CropCycle, CropCycle.field_id == Field.id)
        .where(
            Farmer.is_active.is_(True),
            CropCycle.is_active.is_(True),
            func.lower(CropCycle.crop_name) == (alert.crop_name or "").lower(),
        )
        .distinct()
    )
    if alert.scope == "village" and alert.village:
        stmt = stmt.where(func.lower(Farmer.village) == alert.village.lower())
    elif alert.scope == "tehsil" and alert.tehsil:
        stmt = stmt.where(func.lower(Farmer.tehsil) == alert.tehsil.lower())
    elif alert.scope == "district" and alert.district:
        stmt = stmt.where(func.lower(Farmer.district) == alert.district.lower())

    res = await db.execute(stmt)
    reporters = set(alert.reporting_farmer_ids or [])
    return [f for f in res.scalars().all() if f.id not in reporters]


async def write_outbreak_memory_atoms(
    db: AsyncSession,
    alert: AlertCluster,
    target_farmers: Iterable[Farmer],
) -> int:
    """Persist one OutbreakAlert memory atom per matched farmer."""
    n = 0
    for farmer in target_farmers:
        atom = MemoryAtom(
            id=str(uuid4()),
            farmer_id=farmer.id,
            field_id=None,
            crop_cycle_id=None,
            atom_type="outbreak_alert",
            summary=format_warning_message(alert),
            details={
                "alert_id": alert.id,
                "scope": alert.scope,
                "pest_or_disease": alert.pest_or_disease,
                "crop_name": alert.crop_name,
                "reporters": len(alert.reporting_farmer_ids or []),
                "severity": alert.severity,
            },
            confidence=alert.severity or 0.6,
            source_type="outbreak_service",
            source_id=alert.id,
            village=farmer.village,
            tehsil=farmer.tehsil,
            district=farmer.district,
            event_at=utc_now(),
            is_private=False,
            is_shareable=True,
        )
        db.add(atom)
        n += 1
    if n:
        await db.commit()
    return n


async def fetch_pending_outbreak_for_farmer(
    db: AsyncSession,
    farmer_id: str,
) -> AlertCluster | None:
    """
    Latest live outbreak alert that has notified this farmer but has NOT
    yet had its banner prepended to a reply. Used by agent step 8.
    """
    farmer = await _farmer_for(db, farmer_id)
    if farmer is None:
        return None
    active_crop = await _active_crop_for(db, farmer_id)
    now = utc_now()
    stmt = (
        select(AlertCluster)
        .where(
            AlertCluster.kind == AlertKind.OUTBREAK,
            AlertCluster.expires_at > now,
        )
        .order_by(desc(AlertCluster.created_at))
    )
    res = await db.execute(stmt)
    for alert in res.scalars().all():
        if active_crop and (alert.crop_name or "").lower() != active_crop.lower():
            continue
        notified = set(alert.notified_farmer_ids or [])
        consumed = set(alert.consumed_farmer_ids or [])
        if farmer_id in notified and farmer_id not in consumed:
            return alert
    return None


async def mark_outbreak_consumed(
    db: AsyncSession,
    alert: AlertCluster,
    farmer_id: str,
) -> None:
    consumed = set(alert.consumed_farmer_ids or [])
    if farmer_id in consumed:
        return
    consumed.add(farmer_id)
    alert.consumed_farmer_ids = list(consumed)
    await db.commit()


async def mark_outbreak_notified(
    db: AsyncSession,
    alert: AlertCluster,
    farmer_ids: Iterable[str],
) -> None:
    notified = set(alert.notified_farmer_ids or [])
    before = len(notified)
    notified.update(farmer_ids)
    if len(notified) == before:
        return
    alert.notified_farmer_ids = list(notified)
    alert.farmers_notified = len(notified)
    await db.commit()


def format_warning_message(alert: AlertCluster) -> str:
    """Bilingual one-liner suitable for push + prepend."""
    scope_label = {
        "village": alert.village or "गाँव",
        "tehsil": alert.tehsil or "तहसील",
        "district": alert.district or "जिला",
    }.get(alert.scope or "", alert.district or "")
    threat = (alert.pest_or_disease or "issue").title()
    crop = (alert.crop_name or "your crop").title()
    return (
        f"⚠️ चेतावनी / Outbreak warning — {threat} reported by nearby {crop} farmers "
        f"in {scope_label}. कृपया अपनी फसल जाँचें और निवारक उपाय अपनाएँ। "
        f"Please inspect your {crop} crop and apply preventive measures."
    )
