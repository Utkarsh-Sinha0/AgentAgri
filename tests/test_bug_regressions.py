"""
AgriMesh V4.0 — Bug Regression Suite (Sprint 5, Phase B)

One test (or a small cluster) per bug from the SOTA plan. Each test reaches
the smallest surface area that proves the bug stays fixed — typically a
service-layer function, never the Telegram framework or the full agent
pipeline (those are covered by tests/test_agent_e2e.py).

Cross-references the breakdown in features_sota_breakdown.md.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta

import pytest

from app.models import (
    Advisory,
    AlertCluster,
    CropCycle,
    Farmer,
    Field,
    Observation,
    WikiArticle,
)
from app.models_memory import MemoryAtom
from app.services.memory import (
    extract_from_observation,
    extract_from_outcome,
    get_causal_chain,
    retrieve_memory_context,
    retrieve_similar_farm_context,
)
from app.services.pattern_discovery import discover_patterns
from app.services.verifier import EvidenceBundle, Recommendation, VerifierService
from app.utils.security import hash_password
from app.utils.time import utc_now

# ─── Helpers ───────────────────────────────────────────────────────────


async def _mk_farmer(db, *, phone="9111111111", district="Patna", village="Bhusaula") -> Farmer:
    f = Farmer(
        id=str(uuid.uuid4()),
        phone=phone,
        hashed_password=hash_password("test"),
        name="Test Farmer",
        district=district,
        tehsil="Bihta",
        village=village,
        preferred_language="hi",
    )
    db.add(f)
    await db.flush()
    return f


async def _mk_field_and_cycle(db, farmer: Farmer, crop="rice") -> tuple[Field, CropCycle]:
    field = Field(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        name="Plot A",
        area_acres=2.0,
        soil_type="clay_loam",
    )
    db.add(field)
    await db.flush()
    cycle = CropCycle(
        id=str(uuid.uuid4()),
        field_id=field.id,
        crop_name=crop,
        sowing_date=utc_now() - timedelta(days=30),
        current_stage="vegetative",
        is_active=True,
    )
    db.add(cycle)
    await db.flush()
    return field, cycle


async def _mk_observation(db, farmer, field, cycle, *, text="Brown spots on leaves") -> Observation:
    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_type="text",
        text_content=text,
    )
    db.add(obs)
    await db.flush()
    return obs


async def _mk_advisory(db, obs: Observation, *, risk="PREVENTIVE_ACTION", article_ids=None) -> Advisory:
    adv = Advisory(
        id=str(uuid.uuid4()),
        observation_id=obs.id,
        farmer_id=obs.farmer_id,
        risk_level=risk,
        confidence="MEDIUM",
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=["Spray neem-oil at 5 ml/L"],
        warnings_text=[],
        contextualization="Likely fungal blight",
        evidence_article_ids=article_ids or [],
    )
    db.add(adv)
    await db.flush()
    return adv


# ─── Bug 1: pattern_discovery writes to correlated_with, NOT causes_of ──


async def test_bug1_pattern_discovery_writes_correlated_not_causal(db_session):
    """Co-occurrence is correlation. The job must never write to causes_of."""
    # Build two articles that will co-appear in 3 advisories so the
    # co-occurrence threshold (>=3) trips.
    art1 = WikiArticle(
        id=str(uuid.uuid4()),
        title="Rice blast",
        content="x",
        summary="x",
        causes_of=[],
        correlated_with=[],
    )
    art2 = WikiArticle(
        id=str(uuid.uuid4()),
        title="Bacterial leaf streak",
        content="x",
        summary="x",
        causes_of=[],
        correlated_with=[],
    )
    db_session.add_all([art1, art2])
    await db_session.flush()

    farmer = await _mk_farmer(db_session)
    field, cycle = await _mk_field_and_cycle(db_session, farmer)
    for _ in range(3):
        obs = await _mk_observation(db_session, farmer, field, cycle)
        await _mk_advisory(db_session, obs, article_ids=[art1.id, art2.id])
    await db_session.commit()

    await discover_patterns(db_session)

    refreshed1 = await db_session.get(WikiArticle, art1.id)
    refreshed2 = await db_session.get(WikiArticle, art2.id)

    assert refreshed1.causes_of == [], "Bug 1: causes_of must not be written by pattern discovery"
    assert refreshed2.causes_of == []
    assert art2.id in (refreshed1.correlated_with or [])
    assert art1.id in (refreshed2.correlated_with or [])


# ─── Bug 5: HIGH confidence requires recency or atom-count ──────────────


async def test_bug5_high_confidence_requires_recent_evidence():
    """HIGH with 3 articles but no recent evidence and <5 atoms must fail calibration."""
    verifier = VerifierService()
    rec = Recommendation(confidence="HIGH")
    ev = EvidenceBundle(
        wiki_articles=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        memory_context="",  # no atoms, no dates
    )
    assert verifier._check_calibration(rec, ev) is False


async def test_bug5_high_confidence_passes_with_recent_evidence():
    """HIGH with 3 articles + a memory date within 14 days passes calibration."""
    verifier = VerifierService()
    recent_iso = (utc_now() - timedelta(days=2)).strftime("%Y-%m-%d")
    ev = EvidenceBundle(
        wiki_articles=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        memory_context=f"  - [disease_observed] [{recent_iso}]: atom_type stuff here",
    )
    assert verifier._check_calibration(Recommendation(confidence="HIGH"), ev) is True


async def test_bug5_future_dated_evidence_does_not_count_as_recent():
    """A future YYYY-MM-DD token in memory must not satisfy the recency gate."""
    verifier = VerifierService()
    future_iso = (utc_now() + timedelta(days=10)).strftime("%Y-%m-%d")
    memory = f"  - [disease_observed] [{future_iso}]: text"
    assert verifier._memory_has_recent_evidence(memory, days=14) is False


async def test_bug5_high_confidence_passes_with_many_atoms():
    """HIGH passes when atom_count >= 5 even without a recent date.

    Uses the real rendered format (``  - [<atom_type>] [date]: …``) that
    agent._load_memory_context emits — the counter must match that, not a
    literal "atom_type" token.
    """
    verifier = VerifierService()
    # Use a date well outside the 14-day recency window so the atom counter
    # is the only signal that can pass calibration.
    stale = (utc_now() - timedelta(days=60)).strftime("%Y-%m-%d")
    mem = "\n".join(
        f"  - [disease_observed] [{stale}]: leaf spot entry {i}" for i in range(5)
    )
    ev = EvidenceBundle(
        wiki_articles=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        memory_context=mem,
    )
    assert verifier._check_calibration(Recommendation(confidence="HIGH"), ev) is True


async def test_bug5_high_confidence_atom_counter_rejects_under_threshold():
    """Four atoms + no recent date must NOT satisfy the HIGH gate.

    This pins the counter behaviour so the literal-string regression
    can't return — four bracketed atom lines are below the >=5 floor.
    """
    verifier = VerifierService()
    stale = (utc_now() - timedelta(days=60)).strftime("%Y-%m-%d")
    mem = "\n".join(
        f"  - [disease_observed] [{stale}]: leaf spot entry {i}" for i in range(4)
    )
    ev = EvidenceBundle(
        wiki_articles=[{"id": "a"}, {"id": "b"}, {"id": "c"}],
        memory_context=mem,
    )
    assert verifier._check_calibration(Recommendation(confidence="HIGH"), ev) is False


# ─── Bug 6: contradiction check uses bilingual markers + falls back ─────


async def test_bug6_contradiction_blocks_when_action_already_completed():
    """Memory has Hindi completion marker + action overlap → flagged contradiction."""
    verifier = VerifierService()
    rec = Recommendation(actions_text=["spray neem-oil suspension on infected leaves"])
    ev = EvidenceBundle(
        memory_context="Farmer पहले neem-oil छिड़क चुके हैं on the crop",
    )
    # No embedder loaded in tests → falls back to keyword overlap.
    ok = await verifier._check_memory_contradiction(rec, ev)
    assert ok is False


async def test_bug6_no_contradiction_without_completion_marker():
    """Without any completion marker, the check must short-circuit to True."""
    verifier = VerifierService()
    rec = Recommendation(actions_text=["spray neem-oil"])
    ev = EvidenceBundle(memory_context="Farmer plans to do something tomorrow")
    assert await verifier._check_memory_contradiction(rec, ev) is True


async def test_bug6_empty_memory_passes():
    verifier = VerifierService()
    rec = Recommendation(actions_text=["spray X"])
    assert await verifier._check_memory_contradiction(rec, EvidenceBundle()) is True


# ─── Bug 7: sowing-date parser (Bihar registration extension) ──────────


def test_bug7_sowing_date_parses_iso():
    from app.bot.telegram_bot import _parse_sowing_date

    dt = _parse_sowing_date("2025-08-15")
    assert dt is not None
    assert (dt.year, dt.month, dt.day) == (2025, 8, 15)


def test_bug7_sowing_date_rejects_future_dates():
    from app.bot.telegram_bot import _parse_sowing_date

    future = (utc_now() + timedelta(days=30)).strftime("%Y-%m-%d")
    assert _parse_sowing_date(future) is None


def test_bug7_sowing_date_skip_returns_none():
    from app.bot.telegram_bot import _parse_sowing_date

    for token in ("skip", "छोड़ें", "", "-"):
        assert _parse_sowing_date(token) is None


def test_bug7_sowing_date_invalid_returns_none():
    from app.bot.telegram_bot import _parse_sowing_date

    assert _parse_sowing_date("not a date") is None
    assert _parse_sowing_date("15-08-2025") is None  # wrong format


# ─── Bug 8: voice handler is registered with the Telegram dispatcher ────


def test_bug8_voice_handler_registered(monkeypatch):
    """create_bot must wire a handler for VOICE messages."""
    from telegram.ext import MessageHandler

    from app.bot import telegram_bot as tb
    from app.bot.telegram_bot import create_bot, handle_voice

    monkeypatch.setattr(tb.settings, "telegram_bot_token", "123:dummy-test-token", raising=False)
    app = create_bot()
    assert app is not None
    voice_handlers = [
        h for handlers in app.handlers.values() for h in handlers
        if isinstance(h, MessageHandler) and h.callback is handle_voice
    ]
    assert voice_handlers, "Bug 8: no MessageHandler wired to handle_voice"


# ─── Bug 9: clustering groups by (district, crop, risk), not crop_cycle ─


async def test_bug9_pattern_discovery_clusters_across_farmers(db_session):
    """Two farmers in the same district + crop + risk should cluster together
    even though their crop_cycle_id differs (previously they never did)."""
    from sqlalchemy import select as _select

    f1 = await _mk_farmer(db_session, phone="9000000001", district="Saran", village="Sonepur")
    f2 = await _mk_farmer(db_session, phone="9000000002", district="Saran", village="Sonepur")
    f3 = await _mk_farmer(db_session, phone="9000000003", district="Saran", village="Sonepur")

    for farmer in (f1, f2, f3):
        field, cycle = await _mk_field_and_cycle(db_session, farmer, crop="wheat")
        obs = await _mk_observation(db_session, farmer, field, cycle, text="yellow patches")
        await _mk_advisory(db_session, obs, risk="WATCH")

    await db_session.commit()
    result = await discover_patterns(db_session)

    assert result["new_clusters"] >= 1, (
        "Bug 9: 3 wheat farmers in Saran with WATCH must form a cluster"
    )

    clusters = (await db_session.execute(_select(AlertCluster))).scalars().all()
    target = next(
        (c for c in clusters if c.district == "Saran" and c.crop_name == "wheat"),
        None,
    )
    assert target is not None, "expected (Saran, wheat) cluster"
    assert target.farmer_count >= 3


# ─── Sprint 3 SOTA: M3 causal chain wiring ─────────────────────────────


async def test_m3_observation_atoms_form_causal_chain(db_session):
    """extract_from_observation links obs → vision → advisory in order."""
    farmer = await _mk_farmer(db_session, phone="9222222222")
    field, cycle = await _mk_field_and_cycle(db_session, farmer)

    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_type="photo",
        text_content="leaf",
        image_path="data/x.jpg",
        vision_analysis={"label": "blast"},
        vision_confidence=0.82,
    )
    db_session.add(obs)
    await db_session.flush()

    adv = await _mk_advisory(db_session, obs)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    by_type = {a.atom_type: a for a in atoms}
    assert {"observation_recorded", "vision_analysis", "advisory_given"} <= by_type.keys()
    assert by_type["vision_analysis"].causal_predecessor_atom_id == by_type["observation_recorded"].id
    assert by_type["advisory_given"].causal_predecessor_atom_id == by_type["vision_analysis"].id

    # Walking the chain from the advisory atom returns observation + vision.
    chain = await get_causal_chain(db_session, by_type["advisory_given"].id, max_hops=5)
    chain_types = [a.atom_type for a in chain]
    assert "vision_analysis" in chain_types
    assert "observation_recorded" in chain_types


async def test_m3_get_causal_chain_handles_cycle(db_session):
    """Cycle-detection guard prevents infinite loops on malformed chains."""
    a1 = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id="system",
        atom_type="x",
        summary="a",
        event_at=utc_now(),
        confidence=0.5,
    )
    a2 = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id="system",
        atom_type="x",
        summary="b",
        event_at=utc_now(),
        confidence=0.5,
    )
    db_session.add_all([a1, a2])
    await db_session.flush()

    a1.causal_predecessor_atom_id = a2.id
    a2.causal_predecessor_atom_id = a1.id  # cycle
    await db_session.commit()

    chain = await get_causal_chain(db_session, a1.id, max_hops=10)
    # Each atom should appear at most once.
    ids = [a.id for a in chain]
    assert len(ids) == len(set(ids))


# ─── Sprint 3 SOTA: M2 outcome boost propagates along the chain ────────


async def test_m2_outcome_improved_boosts_advisory_atom(db_session):
    """An 'improved' outcome with rating 5 boosts the advisory atom by +0.10."""
    farmer = await _mk_farmer(db_session, phone="9333333333")
    field, cycle = await _mk_field_and_cycle(db_session, farmer)
    obs = await _mk_observation(db_session, farmer, field, cycle)
    adv = await _mk_advisory(db_session, obs)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    adv_atom = next(a for a in atoms if a.atom_type == "advisory_given")
    before = float(adv_atom.confidence or 0.0)

    outcome = await extract_from_outcome(db_session, obs.id, "improved", "worked great", rating=5)
    assert outcome is not None

    await db_session.refresh(adv_atom)
    assert pytest.approx(adv_atom.confidence, abs=1e-3) == min(1.0, before + 0.10)


async def test_m2_outcome_worsened_subtracts_confidence(db_session):
    """A 'worsened' outcome with rating 4 subtracts -0.08."""
    farmer = await _mk_farmer(db_session, phone="9444444444")
    field, cycle = await _mk_field_and_cycle(db_session, farmer)
    obs = await _mk_observation(db_session, farmer, field, cycle)
    adv = await _mk_advisory(db_session, obs)
    atoms = await extract_from_observation(db_session, obs, adv)
    await db_session.commit()

    adv_atom = next(a for a in atoms if a.atom_type == "advisory_given")
    before = float(adv_atom.confidence or 0.0)

    await extract_from_outcome(db_session, obs.id, "worsened", "got worse", rating=4)
    await db_session.refresh(adv_atom)
    assert pytest.approx(adv_atom.confidence, abs=1e-3) == max(0.0, before - 0.08)


async def test_m2_outcome_no_advisory_chain_falls_back(db_session):
    """When the observation has no linked advisory atom, the fallback path
    boosts recent disease/pest atoms instead of silently dropping the
    feedback signal."""
    farmer = await _mk_farmer(db_session, phone="9555555555")
    field, cycle = await _mk_field_and_cycle(db_session, farmer)
    obs = await _mk_observation(db_session, farmer, field, cycle)
    await db_session.flush()  # no advisory

    # Seed a recent disease atom on the same farmer.
    seed = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=field.id,
        atom_type="disease_observed",
        summary="rice blast",
        confidence=0.50,
        source_type="manual",
        event_at=utc_now() - timedelta(days=2),
    )
    db_session.add(seed)
    await db_session.commit()

    await extract_from_outcome(db_session, obs.id, "improved", "", rating=5)
    await db_session.refresh(seed)
    assert seed.confidence > 0.50  # fallback boosted it


# ─── Sprint 3 SOTA: M1 temporal-decay ranking ──────────────────────────


async def test_m1_recent_atoms_outrank_old_atoms(db_session):
    """Two atoms with the same confidence — the more recent one ranks first
    after temporal-decay weighting."""
    farmer = await _mk_farmer(db_session, phone="9666666666")
    field, cycle = await _mk_field_and_cycle(db_session, farmer)

    fresh = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=field.id,
        atom_type="disease_observed",
        summary="recent disease",
        confidence=0.80,
        source_type="observation",
        event_at=utc_now() - timedelta(days=1),
    )
    stale = MemoryAtom(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=field.id,
        atom_type="disease_observed",
        summary="ancient disease",
        confidence=0.80,
        source_type="observation",
        event_at=utc_now() - timedelta(days=120),  # ~4 half-lives
    )
    db_session.add_all([fresh, stale])
    await db_session.commit()

    results = await retrieve_memory_context(
        db_session,
        farmer_id=farmer.id,
        field_id=field.id,
        top_k=5,
    )
    summaries = [r.get("summary", "") for r in results]
    assert summaries, "expected at least one retrieved atom"
    assert summaries[0].startswith("recent disease"), (
        f"M1: fresh atom should rank first, got order: {summaries}"
    )


# ─── Sprint 3 SOTA: M4 cross-farmer privacy gate ───────────────────────


async def test_m4_cross_farm_blocked_below_k_anonymity(db_session):
    """With only 2 peer farmers, cross-farm retrieval must return []."""
    me = await _mk_farmer(db_session, phone="9777777777", district="Gaya")
    peer1 = await _mk_farmer(db_session, phone="9777777778", district="Gaya")
    peer2 = await _mk_farmer(db_session, phone="9777777779", district="Gaya")

    for f in (peer1, peer2):
        atom = MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=f.id,
            atom_type="disease_observed",
            summary="rice blast nearby",
            confidence=0.80,
            source_type="observation",
            district="Gaya",
            event_at=utc_now() - timedelta(days=10),
        )
        db_session.add(atom)
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session,
        farmer_id=me.id,
        crop_name="rice",
        district="Gaya",
        min_farmers_for_privacy=3,
    )
    assert out == [], "M4: must withhold context when only 2 peers"


async def test_m4_cross_farm_released_at_k_threshold(db_session):
    """At 3 peers, anonymized atoms are released — no PII fields in output."""
    me = await _mk_farmer(db_session, phone="9888888888", district="Vaishali")
    peers = [
        await _mk_farmer(db_session, phone=f"988888888{i}", district="Vaishali")
        for i in range(3)
    ]
    for p in peers:
        db_session.add(MemoryAtom(
            id=str(uuid.uuid4()),
            farmer_id=p.id,
            atom_type="disease_observed",
            summary="rice blast detected",
            confidence=0.80,
            source_type="observation",
            district="Vaishali",
            village=p.village,
            event_at=utc_now() - timedelta(days=5),
            is_shareable=True,
        ))
    await db_session.commit()

    out = await retrieve_similar_farm_context(
        db_session,
        farmer_id=me.id,
        crop_name="rice",
        district="Vaishali",
        min_farmers_for_privacy=3,
    )
    assert len(out) >= 1
    for atom in out:
        # Privacy contract: no farmer/field/village/GPS leak.
        assert "farmer_id" not in atom
        assert "field_id" not in atom
        assert "village" not in atom
        assert "lat" not in atom and "lng" not in atom


# ─── Sprint 4 Evidence: E1 / E2 / E3 wired into _build_advisory_display ─


def test_e1_action_citation_attaches_article_title():
    from app.services.agent import AgentOrchestrator

    wiki = [
        {"title": "Rice blast management", "actions": ["a1", "a2"]},
        {"title": "Bacterial leaf streak", "actions": ["b1"]},
    ]
    # action_index=2 → third action overall → article 2 (after walking 2 from art1)
    citation = AgentOrchestrator._build_action_citation(2, wiki, [], [])
    assert "Bacterial leaf streak" in citation


def test_e1_action_citation_empty_when_no_evidence():
    from app.services.agent import AgentOrchestrator

    assert AgentOrchestrator._build_action_citation(0, [], [], []) == ""


def test_e2_confidence_prefix_mapping_covers_all_levels():
    from app.services.agent import AgentOrchestrator

    prefixes = AgentOrchestrator._CONFIDENCE_PREFIX
    assert set(prefixes) == {"LOW", "MEDIUM", "HIGH", "ESCALATE"}
    # Each prefix should be bilingual (contains '/').
    for level, text in prefixes.items():
        assert "/" in text, f"E2: {level} prefix missing bilingual delimiter"


# ─── LOW #9: ESCALATE must reject monitor-only action sets ──────────────


def test_low9_escalate_rejects_monitor_only_actions():
    """ESCALATE risk with all-monitor actions must fail semantic check.

    Earlier the verifier only required len(actions) >= 1 for ESCALATE, so a
    "Monitor the field daily" recommendation could escalate without proposing
    any active intervention.
    """
    verifier = VerifierService()
    rec = Recommendation(
        risk_level="ESCALATE",
        selected_action_indices=[0, 1],
        actions_text=["Monitor the field daily", "Scout for new spots"],
    )
    assert verifier._check_actions_match_risk(rec, EvidenceBundle()) is False


def test_low9_escalate_accepts_active_action_alongside_monitor():
    """Mixed action sets (one active + one monitor) still pass for ESCALATE."""
    verifier = VerifierService()
    rec = Recommendation(
        risk_level="ESCALATE",
        selected_action_indices=[0, 1],
        actions_text=["Contact extension officer immediately", "Scout daily"],
    )
    assert verifier._check_actions_match_risk(rec, EvidenceBundle()) is True


def test_low9_escalate_rejects_hindi_monitor_only_actions():
    """Hindi monitor tokens (देख / जांच) trip the same rejection."""
    verifier = VerifierService()
    rec = Recommendation(
        risk_level="ESCALATE",
        selected_action_indices=[0],
        actions_text=["रोज़ खेत देखें और जांच करें"],
    )
    assert verifier._check_actions_match_risk(rec, EvidenceBundle()) is False


# ─── LOW #10: no-evidence path must record an Advisory ─────────────────


async def test_low10_no_evidence_persists_advisory_with_retrieval_path_none(db_session):
    """When retrieval finds nothing, the agent must still record a minimal
    advisory marked retrieval_path='none' so coverage gaps are queryable."""
    from sqlalchemy import select

    from app.services.agent import AgentContext, AgentOrchestrator
    from app.utils.security import hash_password

    farmer = Farmer(
        id="farmer-no-ev",
        phone="no-ev",
        hashed_password=hash_password("test"),
        name="No Ev",
        district="Munger",
    )
    field = Field(id="field-no-ev", farmer_id=farmer.id, name="N1", area_acres=1.0)
    cycle = CropCycle(
        id="cycle-no-ev",
        field_id=field.id,
        crop_name="rice",
        sowing_date=utc_now(),
        current_stage="vegetative",
        is_active=True,
    )
    obs = Observation(
        id="obs-no-ev",
        farmer_id=farmer.id,
        field_id=field.id,
        crop_cycle_id=cycle.id,
        observation_type="text",
        text_content="something obscure",
    )
    db_session.add_all([farmer, field, cycle, obs])
    await db_session.commit()

    orch = AgentOrchestrator()
    ctx = AgentContext(
        farmer_id=farmer.id,
        message="something obscure",
        language="en",
        observation_id=obs.id,
    )
    resp = await orch._no_evidence_response(db_session, ctx, t0=0.0)

    assert resp.retrieval_path == "none"
    assert resp.advisory_id is not None

    persisted = await db_session.scalar(select(Advisory).where(Advisory.id == resp.advisory_id))
    assert persisted is not None
    assert persisted.retrieval_path == "none"
    assert persisted.observation_id == obs.id
    assert persisted.confidence == "LOW"
    assert persisted.actions_text == []
