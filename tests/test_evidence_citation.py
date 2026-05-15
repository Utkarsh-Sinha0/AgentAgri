"""
AgriMesh V4.0 — Evidence & Citation Suite (Sprint 5, Phase B / file 3)

Locks in the Sprint-4 evidence pipeline (E1 inline citations, E2 bilingual
confidence prefixes, E3 follow-up change detection) at the unit level. The
broader agent flow is exercised by ``test_agent_e2e.py``; this file pins
each helper independently so refactors don't quietly drift citations or
prefixes.
"""
from __future__ import annotations

import uuid

import pytest

from app.models import Advisory, CropCycle, Farmer, Field, Observation
from app.services.agent import AgentContext, AgentOrchestrator
from app.services.verifier import EvidenceBundle, Recommendation
from app.utils.security import hash_password
from app.utils.time import utc_now
from datetime import timedelta


async def _seed_prev_advisory(
    db,
    *,
    risk_level: str = "WATCH",
    confidence: str = "MEDIUM",
    evidence_article_ids: list[str] | None = None,
) -> Advisory:
    """Build farmer/field/cycle/observation/advisory the minimum the DB needs."""
    farmer = Farmer(
        id=str(uuid.uuid4()),
        phone=f"99{uuid.uuid4().int % 100_000_000:08d}",
        hashed_password=hash_password("x"),
        name="F",
        district="Patna",
        tehsil="Bihta",
        village="Bhusaula",
        preferred_language="hi",
    )
    db.add(farmer)
    await db.flush()
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
        crop_name="rice",
        sowing_date=utc_now() - timedelta(days=20),
        current_stage="vegetative",
        is_active=True,
    )
    db.add(cyc)
    await db.flush()
    obs = Observation(
        id=str(uuid.uuid4()),
        farmer_id=farmer.id,
        field_id=fld.id,
        crop_cycle_id=cyc.id,
        observation_type="text",
        text_content="t",
    )
    db.add(obs)
    await db.flush()
    adv = Advisory(
        id=str(uuid.uuid4()),
        observation_id=obs.id,
        farmer_id=farmer.id,
        risk_level=risk_level,
        confidence=confidence,
        selected_action_indices=[0],
        selected_warning_indices=[],
        actions_text=[],
        warnings_text=[],
        contextualization="",
        evidence_article_ids=evidence_article_ids or [],
    )
    db.add(adv)
    await db.flush()
    return adv


# ─── E1: action citation mapping ───────────────────────────────────────


def test_e1_citation_picks_first_article_for_index_zero():
    wiki = [
        {"id": "w1", "title": "Rice blast management", "actions": ["a1", "a2"]},
        {"id": "w2", "title": "Bacterial leaf streak", "actions": ["b1"]},
    ]
    citation = AgentOrchestrator._build_action_citation(0, wiki, [], [])
    assert "Rice blast management" in citation
    assert "Bacterial leaf streak" not in citation


def test_e1_citation_walks_to_second_article_when_first_is_exhausted():
    wiki = [
        {"title": "Rice blast management", "actions": ["a1", "a2"]},
        {"title": "Bacterial leaf streak", "actions": ["b1"]},
    ]
    # actions indices: 0,1 from art1, 2 from art2
    assert "Bacterial leaf streak" in AgentOrchestrator._build_action_citation(2, wiki, [], [])


def test_e1_citation_returns_empty_when_index_out_of_range():
    """Action index past every article's offset → no article cite (no memory/peer either → "")."""
    wiki = [{"title": "X", "actions": ["a1"]}]
    assert AgentOrchestrator._build_action_citation(99, wiki, [], []) == ""


def test_e1_citation_returns_empty_with_no_evidence():
    assert AgentOrchestrator._build_action_citation(0, [], [], []) == ""


def test_e1_citation_omits_article_without_title():
    """Articles without a title field must not crash and must not be cited."""
    wiki = [{"actions": ["a1"]}]  # no title key
    assert AgentOrchestrator._build_action_citation(0, wiki, [], []) == ""


def test_e1_citation_includes_memory_hit_count():
    memory_atoms = [
        {"atom_type": "disease_observed"},
        {"atom_type": "advisory_given"},
        {"atom_type": "expense_logged"},  # filtered out (not actionable)
    ]
    out = AgentOrchestrator._build_action_citation(
        0, [{"title": "T", "actions": ["x"]}], memory_atoms, [],
    )
    # disease + advisory count → "2 similar cases"
    assert "2 similar case" in out


def test_e1_citation_uses_singular_for_one_memory_hit():
    memory_atoms = [{"atom_type": "disease_observed"}]
    out = AgentOrchestrator._build_action_citation(
        0, [{"title": "T", "actions": ["x"]}], memory_atoms, [],
    )
    assert "1 similar case" in out and "cases" not in out


def test_e1_citation_includes_peer_district_when_present():
    peers = [{"district": "Patna"}, {"district": "Patna"}]
    out = AgentOrchestrator._build_action_citation(
        0, [{"title": "T", "actions": ["x"]}], [], peers,
    )
    assert "in Patna" in out


def test_e1_citation_falls_back_to_count_when_no_peer_district():
    peers = [{"foo": "bar"}, {"foo": "bar"}]
    out = AgentOrchestrator._build_action_citation(
        0, [{"title": "T", "actions": ["x"]}], [], peers,
    )
    assert "2 nearby farms" in out


def test_e1_citation_uses_bullet_separator():
    """Multiple citation parts are joined with ' • ' for the farmer-facing chip."""
    out = AgentOrchestrator._build_action_citation(
        0,
        [{"title": "Rice blast management", "actions": ["x"]}],
        [{"atom_type": "disease_observed"}],
        [{"district": "Gaya"}],
    )
    # Wrapped in [ ] and joined with •
    assert out.startswith(" [") and out.endswith("]")
    assert " • " in out


# ─── E1: memory-atom parsing helper ────────────────────────────────────


def test_e1_parse_memory_atoms_empty_returns_empty_list():
    assert AgentOrchestrator._parse_memory_atoms_from_context(None) == []
    assert AgentOrchestrator._parse_memory_atoms_from_context("") == []


def test_e1_parse_memory_atoms_extracts_atom_type_tokens():
    memory = (
        "Recent field memory:\n"
        "  - [disease_observed] [2026-01-12]: rice blast on plot A\n"
        "  - [pest_detected] [2026-01-10]: stem borer\n"
        "  garbage line\n"
        "  - [advisory_given] [2026-01-09]: applied tricyclazole\n"
    )
    out = AgentOrchestrator._parse_memory_atoms_from_context(memory)
    types = [a["atom_type"] for a in out]
    assert types == ["disease_observed", "pest_detected", "advisory_given"]


def test_e1_parse_memory_atoms_ignores_lines_without_bracket():
    memory = "- something without bracket\n  - just a dash\n"
    assert AgentOrchestrator._parse_memory_atoms_from_context(memory) == []


def test_e1_parse_memory_atoms_skips_empty_atom_type():
    """A `- [ ] [date]: ...` line must not produce a phantom atom."""
    memory = "  - [] [2026-01-01]: bad line\n  - [disease_observed] [2026-01-02]: ok\n"
    out = AgentOrchestrator._parse_memory_atoms_from_context(memory)
    assert [a["atom_type"] for a in out] == ["disease_observed"]


# ─── E2: confidence prefix table ───────────────────────────────────────


def test_e2_prefix_table_complete():
    prefixes = AgentOrchestrator._CONFIDENCE_PREFIX
    assert set(prefixes) == {"LOW", "MEDIUM", "HIGH", "ESCALATE"}


def test_e2_prefix_table_all_bilingual():
    for level, text in AgentOrchestrator._CONFIDENCE_PREFIX.items():
        assert "/" in text, f"E2: {level} missing bilingual '/' delimiter"


def test_e2_prefix_table_escalate_has_warning_marker():
    """The ESCALATE preamble must be visually distinguishable to farmers."""
    text = AgentOrchestrator._CONFIDENCE_PREFIX["ESCALATE"]
    assert "⚠️" in text or "urgent" in text.lower() or "तुरंत" in text


def test_e2_prefix_table_high_distinct_from_medium():
    """Calibration regression guard — never let HIGH and MEDIUM read identically."""
    p = AgentOrchestrator._CONFIDENCE_PREFIX
    assert p["HIGH"] != p["MEDIUM"]
    assert p["LOW"] != p["MEDIUM"]


# ─── E3: follow-up change detection ────────────────────────────────────


@pytest.fixture
def orchestrator():
    return AgentOrchestrator()


async def test_e3_no_previous_id_returns_empty(orchestrator, db_session):
    out = await orchestrator._build_change_detection(
        db_session, Recommendation(), EvidenceBundle(), previous_advisory_id=None,
    )
    assert out == ""


async def test_e3_previous_not_found_returns_empty(orchestrator, db_session):
    out = await orchestrator._build_change_detection(
        db_session, Recommendation(), EvidenceBundle(),
        previous_advisory_id="advisory-that-doesnt-exist",
    )
    assert out == ""


async def test_e3_identical_advisory_reports_no_changes(orchestrator, db_session):
    prev = await _seed_prev_advisory(
        db_session, risk_level="WATCH", confidence="MEDIUM",
        evidence_article_ids=["w1"],
    )
    await db_session.commit()

    out = await orchestrator._build_change_detection(
        db_session,
        Recommendation(risk_level="WATCH", confidence="MEDIUM"),
        EvidenceBundle(wiki_articles=[{"id": "w1"}]),
        previous_advisory_id=prev.id,
    )
    assert out == "No significant changes since last advisory"


async def test_e3_risk_change_surfaces_arrow(orchestrator, db_session):
    prev = await _seed_prev_advisory(db_session, risk_level="WATCH", confidence="MEDIUM")
    await db_session.commit()

    out = await orchestrator._build_change_detection(
        db_session,
        Recommendation(risk_level="ESCALATE", confidence="MEDIUM"),
        EvidenceBundle(),
        previous_advisory_id=prev.id,
    )
    assert "WATCH" in out and "ESCALATE" in out and "→" in out


async def test_e3_new_sources_get_counted(orchestrator, db_session):
    prev = await _seed_prev_advisory(
        db_session, risk_level="WATCH", confidence="MEDIUM",
        evidence_article_ids=["w1"],
    )
    await db_session.commit()

    out = await orchestrator._build_change_detection(
        db_session,
        Recommendation(risk_level="WATCH", confidence="MEDIUM"),
        EvidenceBundle(wiki_articles=[{"id": "w1"}, {"id": "w2"}, {"id": "w3"}]),
        previous_advisory_id=prev.id,
    )
    # 2 new sources (w2, w3)
    assert "2 new source" in out


async def test_e3_confidence_upgrade_surfaces_arrow(orchestrator, db_session):
    prev = await _seed_prev_advisory(db_session, risk_level="WATCH", confidence="LOW")
    await db_session.commit()

    out = await orchestrator._build_change_detection(
        db_session,
        Recommendation(risk_level="WATCH", confidence="HIGH"),
        EvidenceBundle(),
        previous_advisory_id=prev.id,
    )
    assert "LOW" in out and "HIGH" in out


# ─── E1+E2+E3 integration: _build_advisory_display top-line ────────────


async def test_display_uses_correct_confidence_prefix(orchestrator, db_session):
    """The header line must embed the E2 prefix for the recommended confidence."""
    ctx = AgentContext(
        farmer_id="f1", message="m", language="hi", is_followup=False,
    )
    rec = Recommendation(
        risk_level="WATCH",
        confidence="HIGH",
        contextualization="संदर्भ / context",
        actions_text=["do X"],
    )
    out = await orchestrator._build_advisory_display(
        db_session, ctx, rec, EvidenceBundle(),
    )
    assert "हम दृढ़ता से सुझाते हैं" in out  # the HIGH prefix


async def test_display_inserts_change_summary_for_followup(orchestrator, db_session):
    """When ctx.is_followup with a real previous_advisory_id, the change summary
    line is rendered into the display block."""
    prev = await _seed_prev_advisory(db_session, risk_level="WATCH", confidence="LOW")
    await db_session.commit()

    ctx = AgentContext(
        farmer_id="f1", message="m", language="hi",
        is_followup=True, previous_advisory_id=prev.id,
    )
    rec = Recommendation(
        risk_level="WATCH", confidence="HIGH",
        contextualization="ctx", actions_text=["x"],
    )
    out = await orchestrator._build_advisory_display(
        db_session, ctx, rec, EvidenceBundle(),
    )
    assert "What changed" in out


async def test_display_skips_change_summary_when_not_followup(orchestrator, db_session):
    """is_followup=False must skip the change-detection block entirely."""
    ctx = AgentContext(
        farmer_id="f1", message="m", language="hi", is_followup=False,
    )
    rec = Recommendation(
        risk_level="NORMAL", confidence="LOW",
        contextualization="ctx", actions_text=["x"],
    )
    out = await orchestrator._build_advisory_display(
        db_session, ctx, rec, EvidenceBundle(),
    )
    assert "What changed" not in out


async def test_display_renders_action_with_citation(orchestrator, db_session):
    """Each action gets the E1 citation suffix when wiki evidence is present."""
    ctx = AgentContext(farmer_id="f1", message="m", language="hi")
    rec = Recommendation(
        risk_level="WATCH", confidence="MEDIUM",
        contextualization="ctx",
        selected_action_indices=[0],
        actions_text=["Spray neem-oil at 5 ml/L"],
    )
    ev = EvidenceBundle(
        wiki_articles=[{"id": "w1", "title": "Rice blast IPM", "actions": ["Spray neem-oil at 5 ml/L"]}],
    )
    out = await orchestrator._build_advisory_display(db_session, ctx, rec, ev)
    assert "Rice blast IPM" in out
    # Action is rendered with the citation chip on the same line.
    action_line = next((l for l in out.splitlines() if "Spray neem-oil" in l), "")
    assert "📚 Rice blast IPM" in action_line


async def test_display_citation_uses_global_action_index_not_display_position(
    orchestrator, db_session
):
    """Regression: selected_action_indices=[2] must cite the second article.

    Earlier code passed the display position (i-1 = 0) instead of the
    persisted global wiki-action index (2), so the FIRST article was always
    cited regardless of which action the LLM actually picked.
    """
    ctx = AgentContext(farmer_id="f1", message="m", language="hi")
    rec = Recommendation(
        risk_level="WATCH", confidence="MEDIUM",
        contextualization="ctx",
        # Pick global action index 2 → first action of the SECOND article.
        selected_action_indices=[2],
        actions_text=["Apply trichoderma to soil"],
    )
    ev = EvidenceBundle(
        wiki_articles=[
            {"id": "w1", "title": "Rice blast IPM",
             "actions": ["Spray neem-oil at 5 ml/L", "Remove infected leaves"]},
            {"id": "w2", "title": "Soil biocontrol",
             "actions": ["Apply trichoderma to soil"]},
        ],
    )
    out = await orchestrator._build_advisory_display(db_session, ctx, rec, ev)
    action_line = next((l for l in out.splitlines() if "trichoderma" in l), "")
    # The right article (Soil biocontrol) must be attributed — not Rice blast.
    assert "📚 Soil biocontrol" in action_line
    assert "Rice blast IPM" not in action_line
