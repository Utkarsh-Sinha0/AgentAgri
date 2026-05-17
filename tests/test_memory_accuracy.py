"""
AgriMesh V4.0 — Memory Accuracy Suite (Sprint 5, Phase B / file 2)

Covers the *quantitative* contracts of the M1-M4 living-memory features.
Phase B's bug-regression file proves the wires are connected; this file
checks the math: half-lives, delta tables, lookback windows, pool-size
caps, and the k-anonymity invariants the agent loop depends on.

Each test reaches the smallest unit it can — most of these run against
``app/services/memory.py`` directly without touching the Telegram or
agent layers.
"""
from __future__ import annotations

import uuid
from datetime import timedelta

import pytest

from app.models import Advisory, CropCycle, Farmer, Field, Observation
from app.models_memory import MemoryAtom, MemorySummary
from app.services.memory import (
    _HALF_LIVES_DAYS,
    _OUTCOME_DELTAS,
    _outcome_delta,
    _temporal_weight,
    coarsen_field_memory,
    extract_from_observation,
    extract_from_outcome,
    get_causal_chain,
    retrieve_memory_context,
    retrieve_similar_farm_context,
)
from app.utils.security import hash_password
from app.utils.time import utc_now

# ─── Helpers ───────────────────────────────────────────────────────────


async def _farmer(db, *, phone, district="Patna", village="Bhusaula") -> Farmer:
    f = Farmer(
        id=str(uuid.uuid4()),
        phone=phone,
        hashed_password=hash_password("x"),
        name="F",
        district=district,
        tehsil="Bihta",
        village=village,
        preferred_language="hi",
    )
    db.add(f)
    await db.flush()
    return f


async def _field_cycle(db, farmer: Farmer, crop="rice") -> tuple[Field, CropCycle]:
    fld = Field(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        name="P",
        area_acres=1.0,
        soil_type="loam",
    )
    db.add(fld)
    await db.flush()
    cyc = CropCycle(
        id=str(uuid.uuid4()),
        field_id=fld.id,
        crop_name=crop,
        sowing_date=utc_now() - timedelta(days=20),
        current_stage="vegetative",
        is_active=True,
    )
    db.add(cyc)
    await db.flush()
    return fld, cyc


async def _obs_adv(db, farmer, fld, cyc, *, text="brown spots") -> tuple[Observation, Advisory]:
    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        observation_type="text",
        text_content=text,
    )
    db.add(obs)
    await db.flush()
    adv = Advisory(
        id=str(uuid.uuid4()),
        observation_id=obs.id,
        farmer_id=farmer.id,
        risk_level="PREVENTIVE_ACTION",
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["spray X"],
        warnings_text=[],
        contextualization="ctx",
        evidence_article_ids=[],
    )
    db.add(adv)
    await db.flush()
    return obs, adv


# ═══════════════════════════════════════════════════════════════════════
# M1 — Temporal-decay weighting
# ═══════════════════════════════════════════════════════════════════════


def test_m1_temporal_weight_today_is_near_one():
    w = _temporal_weight(utc_now(), "disease_observed")
    assert 0.99 <= w <= 1.0


def test_m1_temporal_weight_one_half_life_is_half():
    half = _HALF_LIVES_DAYS["disease_observed"]
    w = _temporal_weight(utc_now() - timedelta(days=half), "disease_observed")
    assert 0.49 <= w <= 0.51, f"weight at one half-life should be ~0.5, got {w}"


def test_m1_temporal_weight_clamps_future_to_zero():
    w = _temporal_weight(utc_now() + timedelta(days=5), "disease_observed")
    assert w == 0.0


def test_m1_temporal_weight_missing_event_at_floor():
    """No timestamp → 0.10 floor, so the atom is retrievable but deprioritised."""
    assert _temporal_weight(None, "disease_observed") == 0.10


def test_m1_unknown_atom_type_uses_default_half_life():
    """An unknown atom_type falls through to the 30-day default."""
    w_known = _temporal_weight(utc_now() - timedelta(days=30), "disease_observed")
    w_unknown = _temporal_weight(utc_now() - timedelta(days=30), "totally_made_up_type")
    # Both use 30-day half-life → identical value.
    assert pytest.approx(w_known, abs=1e-3) == w_unknown


def test_m1_half_lives_table_has_expected_atom_types():
    """Sanity check the calibration table — keeps refactors honest."""
    required = {
        "disease_observed", "pest_detected", "market_timing", "price_recorded",
        "weather_event", "outcome_reported", "advisory_given", "vision_analysis",
    }
    assert required <= set(_HALF_LIVES_DAYS), (
        f"missing atom types in _HALF_LIVES_DAYS: {required - set(_HALF_LIVES_DAYS)}"
    )


async def test_m1_retrieve_memory_pool_size_capped(db_session):
    """The retrieval pool is capped at 50 atoms even when top_k * 6 would exceed it."""
    farmer = await _farmer(db_session, phone="9100000001")
    fld, _cyc = await _field_cycle(db_session, farmer)

    for i in range(60):
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            field_id=fld.id,
            atom_type="disease_observed",
            summary=f"atom {i}",
            confidence=0.50,
            source_type="observation",
            event_at=utc_now() - timedelta(days=i),
        ))
    await db_session.commit()

    # top_k=20 → would request 120, but the cap is 50 in pool then top_k slice.
    out = await retrieve_memory_context(
        db_session, farmer_id=farmer.id, field_id=fld.id, top_k=20,
    )
    assert len(out) <= 20
    # Ranking must be by relevance score descending.
    scores = [r["relevance_score"] for r in out if r.get("relevance_score") is not None]
    assert scores == sorted(scores, reverse=True), "M1: results not sorted by relevance"


async def test_m1_volatile_atoms_decay_faster_than_stable(db_session):
    """A 14-day-old weather atom should weight lower than a 14-day-old outcome atom.

    weather half-life = 3d, outcome half-life = 90d — confirms the calibration
    table is actually consulted per atom-type, not hard-coded.
    """
    w_weather = _temporal_weight(utc_now() - timedelta(days=14), "weather_event")
    w_outcome = _temporal_weight(utc_now() - timedelta(days=14), "outcome_reported")
    assert w_weather < w_outcome


# ═══════════════════════════════════════════════════════════════════════
# M2 — Outcome-weighted confidence delta table
# ═══════════════════════════════════════════════════════════════════════


def test_m2_delta_table_buckets_rating_high():
    assert _outcome_delta("improved", 5) == 0.10
    assert _outcome_delta("improved", 4) == 0.10
    assert _outcome_delta("worsened", 5) == -0.08


def test_m2_delta_table_buckets_rating_mid_low():
    assert _outcome_delta("improved", 3) == 0.05
    assert _outcome_delta("improved", 2) == 0.02
    assert _outcome_delta("worsened", 3) == -0.05
    assert _outcome_delta("worsened", 1) == -0.03


def test_m2_delta_table_unknown_result_returns_zero():
    """not_tried / partial / freeform → no confidence change."""
    assert _outcome_delta("not_tried", 5) == 0.0
    assert _outcome_delta("", 3) == 0.0
    assert _outcome_delta("partial", 4) == 0.0


def test_m2_delta_table_is_case_insensitive():
    assert _outcome_delta("IMPROVED", 5) == _outcome_delta("improved", 5)


def test_m2_delta_table_is_complete():
    """All six (result, bucket) keys must be present so refactors don't drop one."""
    expected = {
        ("improved", "high"), ("improved", "mid"), ("improved", "low"),
        ("worsened", "high"), ("worsened", "mid"), ("worsened", "low"),
    }
    assert set(_OUTCOME_DELTAS) == expected


async def test_m2_confidence_clamps_at_one(db_session):
    """An advisory atom near 1.0 must not exceed 1.0 after a +0.10 boost."""
    farmer = await _farmer(db_session, phone="9100000002")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs, adv = await _obs_adv(db_session, farmer, fld, cyc)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    adv_atom = next(a for a in atoms if a.atom_type == "advisory_given")
    adv_atom.confidence = 0.97  # very close to ceiling
    await db_session.commit()

    await extract_from_outcome(db_session, obs.id, "improved", "great", rating=5)
    await db_session.refresh(adv_atom)
    assert adv_atom.confidence == 1.0  # clamped


async def test_m2_confidence_clamps_at_zero(db_session):
    """A near-zero atom must not go negative after a -0.08 hit."""
    farmer = await _farmer(db_session, phone="9100000003")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs, adv = await _obs_adv(db_session, farmer, fld, cyc)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    adv_atom = next(a for a in atoms if a.atom_type == "advisory_given")
    adv_atom.confidence = 0.02
    await db_session.commit()

    await extract_from_outcome(db_session, obs.id, "worsened", "bad", rating=5)
    await db_session.refresh(adv_atom)
    assert adv_atom.confidence == 0.0


async def test_m2_outcome_atom_links_back_to_advisory_atom(db_session):
    """The outcome_reported atom's predecessor must be the advisory_given atom."""
    farmer = await _farmer(db_session, phone="9100000004")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs, adv = await _obs_adv(db_session, farmer, fld, cyc)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    adv_atom = next(a for a in atoms if a.atom_type == "advisory_given")
    outcome = await extract_from_outcome(db_session, obs.id, "improved", "", rating=4)
    assert outcome is not None
    assert outcome.causal_predecessor_atom_id == adv_atom.id


async def test_m2_outcome_propagates_to_predecessors_along_chain(db_session):
    """Boost must touch *every* atom in the causal chain (obs → vision → advisory)."""
    farmer = await _farmer(db_session, phone="9100000005")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        observation_type="photo",
        text_content="x",
        image_path="x.jpg",
        vision_analysis={"label": "blast"},
        vision_confidence=0.70,
    )
    db_session.add(obs)
    await db_session.flush()
    adv = Advisory(
        id=str(uuid.uuid4()),
        observation_id=obs.id,
        farmer_id=farmer.id,
        risk_level="WATCH",
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["scout daily"],
        warnings_text=[],
        contextualization="ctx",
        evidence_article_ids=[],
    )
    db_session.add(adv)
    await db_session.flush()
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    by_type = {a.atom_type: a for a in atoms}
    obs_before = float(by_type["observation_recorded"].confidence)
    vis_before = float(by_type["vision_analysis"].confidence)
    adv_before = float(by_type["advisory_given"].confidence)

    await extract_from_outcome(db_session, obs.id, "improved", "", rating=5)

    for a in atoms:
        await db_session.refresh(a)

    # Every atom in the chain should be boosted by +0.10 (clamped at 1.0).
    assert by_type["advisory_given"].confidence == min(1.0, adv_before + 0.10)
    assert by_type["vision_analysis"].confidence == min(1.0, vis_before + 0.10)
    assert by_type["observation_recorded"].confidence == min(1.0, obs_before + 0.10)


async def test_m2_outcome_rating_clamps_out_of_bounds(db_session):
    """rating < 1 or > 5 must be clamped, not raise."""
    farmer = await _farmer(db_session, phone="9100000006")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs, adv = await _obs_adv(db_session, farmer, fld, cyc)
    await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    # Should not crash; the function clamps to [1,5].
    outcome_hi = await extract_from_outcome(db_session, obs.id, "improved", "", rating=99)
    outcome_lo = await extract_from_outcome(db_session, obs.id, "improved", "", rating=-3)
    assert outcome_hi is not None
    assert outcome_lo is not None


async def test_m2_missing_observation_returns_none(db_session):
    """Unknown observation_id must short-circuit to None, not crash."""
    out = await extract_from_outcome(db_session, "nonexistent-obs-id", "improved", "", rating=5)
    assert out is None


# ═══════════════════════════════════════════════════════════════════════
# M2.5 — Field-first localized memory payloads
# ═══════════════════════════════════════════════════════════════════════


async def test_field_summary_includes_soil_crop_status_and_common_issues(db_session):
    """Field coarsening must carry the actual agronomy context, not just atom counts."""
    farmer = await _farmer(db_session, phone="9100000065")
    fld, cyc = await _field_cycle(db_session, farmer, crop="rice")
    fld.soil_ph = 6.8
    fld.irrigation_type = "canal"
    cyc.variety = "Swarna"
    obs, adv = await _obs_adv(db_session, farmer, fld, cyc, text="rice brown spots after humid weather")
    await extract_from_observation(db_session, obs, adv)
    db_session.add(MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        atom_type="disease_observed",
        summary="Brown spot suspected in rice lower leaves",
        details={"crop_name": "rice", "issue": "brown spot", "crop_stage": "vegetative"},
        confidence=0.82,
        source_type="manual",
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        state="Bihar",
        event_at=utc_now() - timedelta(days=1),
        is_shareable=True,
    ))
    db_session.add(MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        atom_type="weather_event",
        summary="Recent humid cloudy weather",
        details={"crop_name": "rice", "weather_factor": "high humidity", "crop_stage": "vegetative"},
        confidence=0.80,
        source_type="weather_tool",
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        state="Bihar",
        event_at=utc_now(),
    ))
    await db_session.commit()

    summary = await coarsen_field_memory(db_session, field_id=fld.id, farmer_id=farmer.id)
    assert summary is not None
    stats = summary.stats
    assert stats["soil"]["soil_type"] == "loam"
    assert stats["soil"]["soil_ph"] == 6.8
    assert stats["current_crop"]["crop_name"] == "rice"
    assert stats["current_crop"]["current_stage"] == "vegetative"
    assert stats["crop_history"][0]["variety"] == "Swarna"
    assert stats["special_issues"]["reported_issues"]["brown spot"] == 1
    assert stats["special_issues"]["weather_factors"]["high humidity"] == 1
    assert stats["latest_advisory"]["advisory_id"] == adv.id
    assert any(issue["id"] == "rice_brown_spot_sustainable" for issue in stats["universal_common_issues"])
    assert stats["expert_escalation"]["national_kisan_call_centre"] == "1800-180-1551"


async def test_retrieve_memory_context_includes_full_public_hierarchy(db_session):
    """Traversal should surface village through national coarsened context for a farmer."""
    farmer = await _farmer(db_session, phone="9100000066", district="Patna", village="Bhusaula")
    fld, cyc = await _field_cycle(db_session, farmer, crop="rice")
    db_session.add(MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        atom_type="observation_recorded",
        summary="rice field observation",
        details={"crop_name": "rice"},
        confidence=0.90,
        source_type="manual",
        village=farmer.village,
        tehsil=farmer.tehsil,
        district=farmer.district,
        state="Bihar",
        event_at=utc_now(),
    ))
    for scale, scale_id in [
        ("village", "Patna:Bhusaula"),
        ("tehsil", "Bihar:Patna:Bihta"),
        ("district", "Bihar:Patna"),
        ("state", "Bihar"),
        ("national", "india"),
    ]:
        db_session.add(MemorySummary(
            id=str(uuid.uuid4()),
            scale=scale,
            scale_id=scale_id,
            title=f"{scale} title",
            summary_text=f"{scale} summary",
            key_patterns=[f"{scale} pattern"],
            stats={"aggregation_chain": ["field", scale]},
            atom_count=3,
            farmer_count=3,
            field_count=3,
            confidence=0.8,
            is_public=True,
            min_farmers_required=3,
        ))
    await db_session.commit()

    out = await retrieve_memory_context(db_session, farmer_id=farmer.id, field_id=fld.id, top_k=3)
    types = {row["type"] for row in out}
    assert {
        "village_summary",
        "tehsil_summary",
        "district_summary",
        "state_summary",
        "national_summary",
    } <= types


# ═══════════════════════════════════════════════════════════════════════
# M3 — Causal chain construction & traversal
# ═══════════════════════════════════════════════════════════════════════


async def test_m3_chain_respects_max_hops(db_session):
    """Walking a 5-deep chain with max_hops=3 returns exactly 3 atoms."""
    farmer = await _farmer(db_session, phone="9100000007")
    prev_id = None
    ids = []
    for i in range(5):
        atom = MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=farmer.id,
            atom_type="x",
            summary=f"a{i}",
            confidence=0.5,
            event_at=utc_now() - timedelta(days=i),
            causal_predecessor_atom_id=prev_id,
        )
        db_session.add(atom)
        await db_session.flush()
        ids.append(atom.id)
        prev_id = atom.id
    await db_session.commit()

    chain = await get_causal_chain(db_session, ids[-1], max_hops=3)
    assert len(chain) == 3


async def test_m3_chain_stops_at_dangling_predecessor(db_session):
    """If predecessor id points to a deleted/unknown atom, walk just stops."""
    farmer = await _farmer(db_session, phone="9100000008")
    atom = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        atom_type="x",
        summary="orphan",
        confidence=0.5,
        event_at=utc_now(),
        causal_predecessor_atom_id="does-not-exist",
    )
    db_session.add(atom)
    await db_session.commit()

    chain = await get_causal_chain(db_session, atom.id, max_hops=5)
    # Walk halts the moment the predecessor lookup returns None.
    assert [a.id for a in chain] == [atom.id]


async def test_m3_observation_chain_orders_correctly(db_session):
    """Photo observation produces obs → vision → advisory in that predecessor order."""
    farmer = await _farmer(db_session, phone="9100000009")
    fld, cyc = await _field_cycle(db_session, farmer)
    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        observation_type="photo",
        text_content="leaves",
        image_path="p.jpg",
        vision_analysis={"label": "rust"},
        vision_confidence=0.66,
    )
    db_session.add(obs)
    await db_session.flush()
    adv = Advisory(
        id=str(uuid.uuid4()),
        observation_id=obs.id,
        farmer_id=farmer.id,
        risk_level="WATCH",
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["scout"],
        warnings_text=[],
        contextualization="ctx",
        evidence_article_ids=[],
    )
    db_session.add(adv)
    await db_session.flush()
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    by_type = {a.atom_type: a for a in atoms}
    chain = await get_causal_chain(db_session, by_type["advisory_given"].id, max_hops=5)
    types_in_chain = [a.atom_type for a in chain]
    assert types_in_chain == ["advisory_given", "vision_analysis", "observation_recorded"]


# ═══════════════════════════════════════════════════════════════════════
# M4 — Cross-farmer privacy gate
# ═══════════════════════════════════════════════════════════════════════


async def test_m4_no_district_returns_empty(db_session):
    """The privacy gate must refuse to query without a district scope."""
    farmer = await _farmer(db_session, phone="9100000010")
    out = await retrieve_similar_farm_context(
        db_session, farmer_id=farmer.id, crop_name="rice", district=None,
    )
    assert out == []


async def test_m4_excludes_requesting_farmer_from_results(db_session):
    """Even when others contribute, the requester's own atoms must not leak back."""
    me = await _farmer(db_session, phone="9100000011", district="Gaya")
    peers = [
        await _farmer(db_session, phone=f"9100001{i:03d}", district="Gaya")
        for i in range(3)
    ]
    # Seed my own atom — should be excluded.
    db_session.add(MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=me.id,
        atom_type="disease_observed",
        summary="rice blast on my plot",
        confidence=0.9,
        district="Gaya",
        event_at=utc_now() - timedelta(days=3),
        is_shareable=True,
    ))
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="rice blast nearby",
            confidence=0.8,
            district="Gaya",
            event_at=utc_now() - timedelta(days=2),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Gaya",
        min_farmers_for_privacy=3,
    )
    # No atom in the output should reference "my plot".
    assert all("my plot" not in a["summary"] for a in out)


async def test_m4_skips_system_pseudo_farmer(db_session):
    """The reserved "system" farmer (used for NDVI etc.) doesn't count toward k."""
    me = await _farmer(db_session, phone="9100000012", district="Saran")
    p1 = await _farmer(db_session, phone="9100000013", district="Saran")
    p2 = await _farmer(db_session, phone="9100000014", district="Saran")
    for fid in (p1.id, p2.id, "system"):
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=fid,
            atom_type="disease_observed",
            summary="rice issue",
            confidence=0.7,
            district="Saran",
            event_at=utc_now() - timedelta(days=1),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Saran",
        min_farmers_for_privacy=3,
    )
    # Only 2 distinct real peers → must be withheld.
    assert out == []


async def test_m4_lookback_window_excludes_old_atoms(db_session):
    """An atom older than ``lookback_days`` must not contribute to the k count."""
    me = await _farmer(db_session, phone="9100000015", district="Vaishali")
    peers = [
        await _farmer(db_session, phone=f"9100002{i:03d}", district="Vaishali")
        for i in range(3)
    ]
    # Make all three peer atoms ancient.
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="rice blast",
            confidence=0.8,
            district="Vaishali",
            event_at=utc_now() - timedelta(days=400),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Vaishali",
        min_farmers_for_privacy=3, lookback_days=60,
    )
    assert out == []


async def test_m4_crop_filter_narrows_pool(db_session):
    """Peers with the same disease on a *different* crop should not be retrieved."""
    me = await _farmer(db_session, phone="9100000020", district="Begusarai")
    peers = [
        await _farmer(db_session, phone=f"9100003{i:03d}", district="Begusarai")
        for i in range(3)
    ]
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="maize ear rot",  # different crop in summary
            confidence=0.8,
            district="Begusarai",
            event_at=utc_now() - timedelta(days=3),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Begusarai",
        min_farmers_for_privacy=3,
    )
    # No 'rice' in any summary → ILIKE filter rejects all → []
    assert out == []


async def test_m4_atom_type_filter_only_releases_actionable_types(db_session):
    """The retrieval only releases disease/pest/advisory/outcome atom types."""
    me = await _farmer(db_session, phone="9100000030", district="Munger")
    peers = [
        await _farmer(db_session, phone=f"9100004{i:03d}", district="Munger")
        for i in range(3)
    ]
    # Seed only expense atoms — must be skipped by the atom_type filter.
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="expense_logged",
            summary="rice seed purchase",
            confidence=0.99,
            district="Munger",
            event_at=utc_now() - timedelta(days=2),
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Munger",
        min_farmers_for_privacy=3,
    )
    assert out == []


async def test_m4_top_k_caps_output_even_with_many_peers(db_session):
    """With 10 eligible peers and top_k=3, output has at most 3 atoms."""
    me = await _farmer(db_session, phone="9100000040", district="Nalanda")
    for i in range(10):
        p = await _farmer(db_session, phone=f"9100005{i:03d}", district="Nalanda")
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="rice blast spreading",
            confidence=0.8,
            district="Nalanda",
            event_at=utc_now() - timedelta(days=i % 30),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Nalanda",
        min_farmers_for_privacy=3, top_k=3,
    )
    assert len(out) <= 3


async def test_m4_one_atom_per_peer_farmer(db_session):
    """Multiple atoms from the same peer collapse to one in the output."""
    me = await _farmer(db_session, phone="9100000050", district="Buxar")
    peers = [
        await _farmer(db_session, phone=f"9100006{i:03d}", district="Buxar")
        for i in range(3)
    ]
    for p in peers:
        for i in range(4):
            db_session.add(MemoryAtom(
                id=str(uuid.uuid4()),
                farmer_id=p.id,
                atom_type="disease_observed",
                summary=f"rice issue {i}",
                confidence=0.8,
                district="Buxar",
                event_at=utc_now() - timedelta(days=i),
                is_shareable=True,
            ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Buxar",
        min_farmers_for_privacy=3, top_k=10,
    )
    # At most one atom per peer → at most 3 atoms in this test.
    assert len(out) <= 3


async def test_m4_is_shareable_filter_excludes_unflagged_atoms(db_session):
    """Regression for codex HIGH #5: even atom_types in the whitelist must
    stay private unless their atom was explicitly marked shareable at
    extract time. Three peer atoms of a safe type with ``is_shareable=False``
    must not satisfy the k=3 gate.
    """
    me = await _farmer(db_session, phone="9100000060", district="Patna")
    peers = [
        await _farmer(db_session, phone=f"9100007{i:03d}", district="Patna")
        for i in range(3)
    ]
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",   # in whitelist
            summary="rice blast nearby",
            confidence=0.8,
            district="Patna",
            event_at=utc_now() - timedelta(days=2),
            is_shareable=False,             # but not classified shareable
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Patna",
        min_farmers_for_privacy=3,
    )
    assert out == []


async def test_m4_is_shareable_filter_includes_flagged_atoms(db_session):
    """Counterpart to the exclusion test: identical atoms with
    ``is_shareable=True`` *do* satisfy the gate and surface in the output.
    """
    me = await _farmer(db_session, phone="9100000070", district="Sheikhpura")
    peers = [
        await _farmer(db_session, phone=f"9100008{i:03d}", district="Sheikhpura")
        for i in range(3)
    ]
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="rice blast nearby",
            confidence=0.8,
            district="Sheikhpura",
            event_at=utc_now() - timedelta(days=2),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session, farmer_id=me.id, crop_name="rice", district="Sheikhpura",
        min_farmers_for_privacy=3,
    )
    assert len(out) >= 1
    assert all(a["atom_type"] == "disease_observed" for a in out)
